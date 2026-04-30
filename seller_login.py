import os
import json
import asyncio
import logging
from pathlib import Path
from playwright.async_api import async_playwright, BrowserContext, Page

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


async def _do_login(page: Page):
    """Perform full Seller Central login with email + password."""
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
