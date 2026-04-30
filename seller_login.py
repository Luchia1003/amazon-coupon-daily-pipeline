import os
import json
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page

try:
    import pyotp
    _PYOTP_AVAILABLE = True
except ImportError:
    _PYOTP_AVAILABLE = False

logger = logging.getLogger(__name__)

SESSION_DIR = Path("session")
COOKIES_FILE = SESSION_DIR / "cookies.json"
STORAGE_FILE = SESSION_DIR / "storage.json"

SELLER_CENTRAL_URL = "https://sellercentral.amazon.com"
LOGIN_URL = "https://www.amazon.com/ap/signin"


async def _is_session_valid(page: Page) -> bool:
    """Check if current session is still authenticated."""
    try:
        await page.goto(SELLER_CENTRAL_URL + "/home", wait_until="domcontentloaded", timeout=20000)
        # If redirected to login page, session expired
        if "signin" in page.url or "ap/signin" in page.url:
            return False
        return True
    except Exception:
        return False


async def _load_cookies(context: BrowserContext) -> bool:
    """Load saved cookies into browser context. Returns True if cookies loaded."""
    if not COOKIES_FILE.exists():
        return False
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        await context.add_cookies(cookies)
        logger.info("Loaded saved cookies from disk.")
        return True
    except Exception as e:
        logger.warning(f"Failed to load cookies: {e}")
        return False


async def _save_cookies(context: BrowserContext):
    """Persist cookies to disk for future reuse."""
    SESSION_DIR.mkdir(exist_ok=True)
    cookies = await context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f, indent=2)
    logger.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}.")


async def _handle_otp(page: Page):
    """
    Handle Amazon two-factor authentication (OTP) if triggered.

    Supports two modes (checked in order):
      1. TOTP (Authenticator App) — set AMAZON_TOTP_SECRET in env.
         Amazon generates a fresh 6-digit code automatically via pyotp.
         Recommended: switch your Amazon account from SMS to Authenticator App.
      2. Static OTP — set AMAZON_OTP in env before running (manual fallback).

    Also ticks "Don't require OTP on this browser" to extend trusted-device period.
    """
    # Detect OTP page by looking for the OTP input field
    try:
        await page.wait_for_selector('input#auth-mfa-otpcode, input[name="otpCode"]', timeout=8000)
    except Exception:
        # No OTP page appeared — 2FA not triggered this run
        return

    logger.info("OTP / 2FA page detected.")

    # --- Resolve OTP code ---
    totp_secret = os.environ.get("AMAZON_TOTP_SECRET", "").strip()
    static_otp = os.environ.get("AMAZON_OTP", "").strip()

    if totp_secret:
        if not _PYOTP_AVAILABLE:
            raise RuntimeError("AMAZON_TOTP_SECRET is set but pyotp is not installed. Run: pip install pyotp")
        otp_code = pyotp.TOTP(totp_secret).now()
        logger.info("Generated TOTP code via AMAZON_TOTP_SECRET.")
    elif static_otp:
        otp_code = static_otp
        logger.info("Using static OTP from AMAZON_OTP env var.")
    else:
        raise RuntimeError(
            "Amazon requires OTP but neither AMAZON_TOTP_SECRET nor AMAZON_OTP is set.\n"
            "  Recommended fix: switch your Amazon account from SMS to Authenticator App,\n"
            "  copy the setup key, and set AMAZON_TOTP_SECRET=<key> in your .env / GitHub Secrets."
        )

    # Fill OTP code
    await page.fill('input#auth-mfa-otpcode, input[name="otpCode"]', otp_code)

    # Check "trust this device" to avoid repeated OTP prompts (~30 days)
    remember_box = page.locator('input#auth-mfa-remember-device, input[name="rememberDevice"]')
    if await remember_box.count() > 0:
        await remember_box.check()
        logger.info("Checked 'remember this device'.")

    submit_btn = page.locator('input#auth-signin-button, input[type="submit"]').first
    await submit_btn.click()
    logger.info("OTP submitted.")


async def _do_login(page: Page):
    """Perform full Seller Central login with email + password, handling OTP if required."""
    email = os.environ["AMAZON_EMAIL"]
    password = os.environ["AMAZON_PASSWORD"]

    logger.info("Navigating to Seller Central login...")
    await page.goto(SELLER_CENTRAL_URL, wait_until="domcontentloaded", timeout=30000)

    # Enter email
    await page.wait_for_selector('input[type="email"], input#ap_email', timeout=15000)
    await page.fill('input[type="email"], input#ap_email', email)

    continue_btn = page.locator('input#continue, input[type="submit"]').first
    await continue_btn.click()

    # Enter password
    await page.wait_for_selector('input[type="password"], input#ap_password', timeout=15000)
    await page.fill('input[type="password"], input#ap_password', password)

    sign_in_btn = page.locator('input#signInSubmit, input[type="submit"]').first
    await sign_in_btn.click()

    # Handle OTP / 2FA if Amazon triggers it
    await _handle_otp(page)

    # Wait for redirect to Seller Central home
    await page.wait_for_url("**/sellercentral.amazon.com/**", timeout=30000)
    logger.info(f"Login successful. Current URL: {page.url}")


async def get_authenticated_page() -> tuple:
    """
    Return (playwright, browser, context, page) with a valid authenticated session.
    Reuses existing session cookies if still valid; otherwise performs fresh login.
    """
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )

    page = await context.new_page()
    cookies_loaded = await _load_cookies(context)

    if cookies_loaded:
        valid = await _is_session_valid(page)
        if valid:
            logger.info("Existing session is valid — skipping login.")
            return playwright, browser, context, page
        else:
            logger.info("Saved session expired — performing fresh login.")

    await _do_login(page)
    await _save_cookies(context)
    return playwright, browser, context, page


async def close_session(playwright, browser):
    """Cleanly close browser and playwright."""
    await browser.close()
    await playwright.stop()
