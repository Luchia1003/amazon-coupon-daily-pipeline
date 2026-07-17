"""
Amazon Seller Central session via exported browser cookies.

How to get your cookies (one-time setup, repeat when session expires):
  1. Log into https://sellercentral.amazon.com in Chrome
  2. Open DevTools (F12) → Application → Cookies → sellercentral.amazon.com
  3. Install Chrome extension "Cookie-Editor" (or similar)
  4. Click Export → "Export as JSON"
  5. Copy the full JSON string
  6. Set it as AMAZON_SESSION_COOKIES in your .env file or GitHub Secret
"""

import os
import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SESSION_DIR = Path("session")
COOKIES_FILE = SESSION_DIR / "cookies.json"

SELLER_CENTRAL_URL = "https://sellercentral.amazon.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _load_cookies_from_env(session: requests.Session) -> bool:
    """
    Load cookies from AMAZON_SESSION_COOKIES environment variable.
    Accepts two formats:
      - JSON array (from Cookie-Editor export): [{"name": "x", "value": "y", ...}, ...]
      - Plain key=value string (from DevTools request headers): session-id=xxx; ubid-main=yyy
    """
    raw = os.environ.get("AMAZON_SESSION_COOKIES", "").strip()
    if not raw:
        return False

    try:
        data = json.loads(raw)
        # Cookie-Editor format: list of cookie objects
        if isinstance(data, list):
            for c in data:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".amazon.com"))
        # Dict format: {name: value}
        elif isinstance(data, dict):
            for name, value in data.items():
                session.cookies.set(name, value)
        logger.info(f"Loaded {len(session.cookies)} cookies from AMAZON_SESSION_COOKIES.")
        return True
    except json.JSONDecodeError:
        # Plain "key=value; key2=value2" string from DevTools
        pairs = [p.strip() for p in raw.split(";") if "=" in p]
        for pair in pairs:
            name, _, value = pair.partition("=")
            session.cookies.set(name.strip(), value.strip())
        logger.info(f"Loaded {len(session.cookies)} cookies from cookie string.")
        return True


def _load_cookies_from_file(session: requests.Session) -> bool:
    """Load previously saved cookies from disk."""
    if not COOKIES_FILE.exists():
        return False
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        if isinstance(cookies, list):
            for c in cookies:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".amazon.com"))
        else:
            session.cookies.update(cookies)
        logger.info(f"Loaded {len(session.cookies)} cookies from {COOKIES_FILE}.")
        return True
    except Exception as e:
        logger.warning(f"Could not load cookie file: {e}")
        return False


def _save_cookies_to_file(session: requests.Session):
    SESSION_DIR.mkdir(exist_ok=True)
    cookies = [{"name": k, "value": v} for k, v in session.cookies.items()]
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    logger.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}.")


def _is_session_valid(session: requests.Session) -> bool:
    """Check if cookies give us access to Seller Central."""
    try:
        resp = session.get(
            SELLER_CENTRAL_URL + "/home",
            headers=HEADERS,
            allow_redirects=True,
            timeout=15,
        )
        # DEBUG: full redirect chain so we can see where Amazon sends us
        for h in resp.history:
            logger.info(f"DEBUG redirect: {h.status_code} {h.url} -> {h.headers.get('Location', '')}")
        logger.info(f"DEBUG final: {resp.status_code} {resp.url} | body_len={len(resp.text)}")
        title_start = resp.text.find("<title>")
        if title_start != -1:
            logger.info(f"DEBUG title: {resp.text[title_start:title_start + 120]!r}")
        cookie_names = sorted(session.cookies.keys())
        logger.info(f"DEBUG cookie names sent: {cookie_names}")
        # DEBUG: probe the actual coupon API — /home may demand fresh MFA while APIs still work
        try:
            api_resp = session.get(
                SELLER_CENTRAL_URL + "/coupons/api/getCouponPromotions",
                params={"paginationSize": 1, "paginationSkip": 0, "clientId": "LegacyCouponsUI"},
                headers={
                    **HEADERS,
                    "Referer": SELLER_CENTRAL_URL + "/coupons",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=20,
            )
            ctype = api_resp.headers.get("Content-Type", "")
            logger.info(
                f"DEBUG coupon API probe: {api_resp.status_code} {api_resp.url[:120]} | "
                f"content-type={ctype} | body[:200]={api_resp.text[:200]!r}"
            )
        except Exception as e:
            logger.info(f"DEBUG coupon API probe failed: {e}")
        if "signin" in resp.url or "ap/signin" in resp.url:
            logger.warning("Session invalid — redirected to login page.")
            return False
        logger.info(f"Session valid. Status: {resp.status_code}")
        return True
    except Exception as e:
        logger.warning(f"Session check failed: {e}")
        return False


def get_session() -> requests.Session:
    """
    Return an authenticated requests.Session.

    Priority:
      1. AMAZON_SESSION_COOKIES env var (GitHub Secret / .env)
      2. Cached cookies from disk (session/cookies.json)

    If neither works, raises a clear error with instructions.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Try env var first (GitHub Actions)
    if _load_cookies_from_env(session):
        if _is_session_valid(session):
            _save_cookies_to_file(session)
            return session
        logger.warning("Cookies from AMAZON_SESSION_COOKIES are expired or invalid.")

    # Fallback: cached cookies from previous run
    session.cookies.clear()
    if _load_cookies_from_file(session):
        if _is_session_valid(session):
            logger.info("Using cached cookies from disk.")
            return session
        logger.warning("Cached cookies are expired.")

    raise RuntimeError(
        "\n"
        "═══════════════════════════════════════════════════════\n"
        "  Amazon session expired — please refresh your cookies.\n"
        "═══════════════════════════════════════════════════════\n"
        "\n"
        "Steps:\n"
        "  1. Log into https://sellercentral.amazon.com in Chrome\n"
        "  2. Install Chrome extension: Cookie-Editor\n"
        "     https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm\n"
        "  3. Click the extension → Export → 'Export as JSON'\n"
        "  4. Copy the full JSON text\n"
        "  5. Update AMAZON_SESSION_COOKIES in GitHub Secrets (or .env)\n"
        "  6. Re-run the pipeline\n"
    )
