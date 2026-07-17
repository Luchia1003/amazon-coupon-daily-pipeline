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
        if "signin" in resp.url or "ap/signin" in resp.url:
            logger.warning("Session invalid — redirected to login page.")
            return False
        logger.info(f"Session valid. Status: {resp.status_code}")
        return True
    except Exception as e:
        logger.warning(f"Session check failed: {e}")
        return False


def _login_with_browser(session: requests.Session) -> bool:
    """
    Fresh Playwright login (email + password + TOTP), then transfer the
    browser's cookies into the requests session.

    Needed since ~2026-07-15: Amazon rejects replayed browser cookies coming
    from a different IP (runner gets bounced to /ap/signin with
    max_auth_age=300), but a full login from the runner's own IP still works.
    """
    if not os.environ.get("AMAZON_EMAIL") or not os.environ.get("AMAZON_PASSWORD"):
        logger.info("AMAZON_EMAIL/AMAZON_PASSWORD not set — skipping browser login.")
        return False
    try:
        from browser_login import login_and_get_cookies

        logger.info("Falling back to Playwright browser login...")
        cookies, user_agent = login_and_get_cookies()
        session.cookies.clear()
        # Match the exact UA the session was established under
        session.headers["User-Agent"] = user_agent
        HEADERS["User-Agent"] = user_agent
        for c in cookies:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".amazon.com"))
        logger.info(f"Browser login OK — {len(cookies)} cookies transferred.")
        return True
    except Exception as e:
        logger.warning(f"Browser login failed: {e}")
        return False


def get_session() -> requests.Session:
    """
    Return an authenticated requests.Session.

    Priority:
      1. AMAZON_SESSION_COOKIES env var (GitHub Secret / .env)
      2. Cached cookies from disk (session/cookies.json)
      3. Fresh Playwright browser login (AMAZON_EMAIL / AMAZON_PASSWORD / AMAZON_TOTP_SECRET)

    If none works, raises a clear error with instructions.
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

    # Fallback: fresh browser login from the runner's own IP
    if _login_with_browser(session):
        if _is_session_valid(session):
            _save_cookies_to_file(session)
            return session
        logger.warning("Browser-login cookies did not validate.")

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
