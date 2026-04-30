"""
Amazon Seller Central login using requests.Session.
Persists cookies to disk so login only happens when session expires.
Handles TOTP two-factor authentication automatically.
"""

import os
import re
import json
import logging
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

try:
    import pyotp
    _PYOTP_AVAILABLE = True
except ImportError:
    _PYOTP_AVAILABLE = False

logger = logging.getLogger(__name__)

SESSION_DIR = Path("session")
COOKIES_FILE = SESSION_DIR / "cookies.json"

SELLER_CENTRAL_URL = "https://sellercentral.amazon.com"
LOGIN_URL = "https://www.amazon.com/ap/signin"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _save_cookies(session: requests.Session):
    SESSION_DIR.mkdir(exist_ok=True)
    cookies = {k: v for k, v in session.cookies.items()}
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    logger.info(f"Saved {len(cookies)} cookies.")


def _load_cookies(session: requests.Session) -> bool:
    if not COOKIES_FILE.exists():
        return False
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        session.cookies.update(cookies)
        logger.info("Loaded saved cookies from disk.")
        return True
    except Exception as e:
        logger.warning(f"Could not load cookies: {e}")
        return False


def _is_session_valid(session: requests.Session) -> bool:
    """Hit the Seller Central home page — if redirected to login, session expired."""
    try:
        resp = session.get(SELLER_CENTRAL_URL + "/home", allow_redirects=True, timeout=15)
        if "signin" in resp.url or "ap/signin" in resp.url:
            return False
        return resp.status_code == 200
    except Exception:
        return False


def _extract_hidden_fields(html: str) -> dict:
    """Pull all hidden <input> fields from a form (CSRF tokens etc.)."""
    return dict(re.findall(r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', html))


def _get_otp_code() -> str:
    totp_secret = os.environ.get("AMAZON_TOTP_SECRET", "").strip()
    static_otp = os.environ.get("AMAZON_OTP", "").strip()

    if totp_secret:
        if not _PYOTP_AVAILABLE:
            raise RuntimeError("AMAZON_TOTP_SECRET set but pyotp not installed.")
        code = pyotp.TOTP(totp_secret).now()
        logger.info("Generated TOTP code automatically.")
        return code
    elif static_otp:
        logger.info("Using static OTP from AMAZON_OTP env var.")
        return static_otp
    else:
        raise RuntimeError(
            "Amazon requires OTP but neither AMAZON_TOTP_SECRET nor AMAZON_OTP is set.\n"
            "Switch your Amazon account to Authenticator App and set AMAZON_TOTP_SECRET."
        )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _do_login(session: requests.Session):
    email = os.environ["AMAZON_EMAIL"]
    password = os.environ["AMAZON_PASSWORD"]

    logger.info("Starting Amazon Seller Central login...")

    # Step 1: Load Seller Central to get redirected to login page
    resp = session.get(SELLER_CENTRAL_URL, headers=HEADERS, timeout=20)
    hidden = _extract_hidden_fields(resp.text)

    # Step 2: Submit email
    login_resp = session.post(
        LOGIN_URL,
        data={**hidden, "email": email, "create": "0"},
        headers={**HEADERS, "Referer": resp.url},
        timeout=20,
    )

    # Step 3: Submit password
    hidden2 = _extract_hidden_fields(login_resp.text)
    pwd_resp = session.post(
        LOGIN_URL,
        data={**hidden2, "password": password, "rememberMe": "true"},
        headers={**HEADERS, "Referer": login_resp.url},
        timeout=20,
        allow_redirects=True,
    )

    # Step 4: Handle OTP if triggered
    if "auth-mfa-otpcode" in pwd_resp.text or "otpCode" in pwd_resp.text or "verification" in pwd_resp.url:
        logger.info("OTP page detected.")
        otp_code = _get_otp_code()
        hidden3 = _extract_hidden_fields(pwd_resp.text)
        otp_resp = session.post(
            pwd_resp.url,
            data={
                **hidden3,
                "otpCode": otp_code,
                "mfaSubmit": "Submit",
                "rememberDevice": "",   # trust this device
            },
            headers={**HEADERS, "Referer": pwd_resp.url},
            timeout=20,
            allow_redirects=True,
        )
        final_resp = otp_resp
    else:
        final_resp = pwd_resp

    if "signin" in final_resp.url or "ap/signin" in final_resp.url:
        raise RuntimeError(f"Login failed — still on login page: {final_resp.url}")

    logger.info(f"Login successful. URL: {final_resp.url}")


def get_session() -> requests.Session:
    """
    Return an authenticated requests.Session.
    Reuses saved cookies if still valid; otherwise performs fresh login.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    if _load_cookies(session) and _is_session_valid(session):
        logger.info("Existing session is valid — skipping login.")
        return session

    logger.info("Session expired or not found — logging in.")
    _do_login(session)
    _save_cookies(session)
    return session
