"""
Playwright-based Seller Central login (fallback when exported cookies fail).

Since ~2026-07-15 Amazon rejects replayed browser cookies coming from a
different IP (datacenter runners get bounced to /ap/signin with
openid.pape.max_auth_age=300). A fresh full login from the runner's own IP
still works — same approach as browser_report.py in amazon-snowflake-sync.

Required env vars:
    AMAZON_EMAIL        – Seller Central login email
    AMAZON_PASSWORD     – Seller Central login password
Optional:
    AMAZON_TOTP_SECRET  – base-32 TOTP secret for MFA accounts
"""

import os
import logging

logger = logging.getLogger(__name__)

SC_BASE = "https://sellercentral.amazon.com"


def login_and_get_cookies() -> tuple[list[dict], str]:
    """Log in with Playwright, return (cookies, user_agent) of the session."""
    from playwright.sync_api import sync_playwright

    email = os.environ["AMAZON_EMAIL"]
    password = os.environ["AMAZON_PASSWORD"]
    otp_secret = os.environ.get("AMAZON_TOTP_SECRET")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto(f"{SC_BASE}/gp/homepage.html", wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(3_000)
        logger.info(f"[browser] After homepage load → {page.url}")

        if "signin" in page.url.lower() or "ap/signin" in page.url.lower():
            page.locator("#ap_email, input[name='email'], input[type='email']").first.fill(
                email, timeout=15_000
            )
            page.wait_for_timeout(800)
            page.locator("#continue, input[id='continue'], button[type='submit']").first.click(
                timeout=10_000
            )
            page.wait_for_timeout(2_000)

            page.locator("#ap_password, input[name='password'], input[type='password']").first.fill(
                password, timeout=10_000
            )
            page.wait_for_timeout(800)
            page.locator("#signInSubmit, input[id='signInSubmit'], button[type='submit']").first.click(
                timeout=10_000
            )
            page.wait_for_timeout(3_000)
            logger.info(f"[browser] After password → {page.url}")

            if otp_secret:
                try:
                    import pyotp

                    otp = pyotp.TOTP(otp_secret).now()
                    page.locator("#auth-mfa-otpcode, input[name='otpCode']").first.fill(
                        otp, timeout=6_000
                    )
                    page.locator("#auth-signin-button, button[type='submit']").first.click(
                        timeout=6_000
                    )
                    page.wait_for_timeout(3_000)
                    logger.info(f"[browser] After OTP → {page.url}")
                except Exception as e:
                    logger.info(f"[browser] OTP step skipped: {e}")

        if (
            "/ap/signin" in page.url
            or "captcha" in page.url.lower()
            or "challenge" in page.url.lower()
        ):
            raise RuntimeError(f"Playwright login failed — still on auth page: {page.url}")

        # Account switcher may appear right after login — select US
        if "account-switcher" in page.url:
            logger.info("[browser] Account switcher — selecting United States...")
            page.locator('button:has-text("United States")').click(timeout=10_000)
            page.wait_for_timeout(800)
            page.locator('button.button:has-text("Select account")').click(timeout=10_000)
            page.wait_for_function(
                "() => !window.location.href.includes('account-switcher')",
                timeout=20_000,
            )
            page.wait_for_timeout(2_000)

        logger.info(f"[browser] Logged in. Final URL: {page.url}")
        user_agent = page.evaluate("navigator.userAgent")
        cookies = context.cookies()
        browser.close()

    return cookies, user_agent
