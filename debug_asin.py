"""
Debug: check raw products API response for a specific ASIN.

Fill in COOKIES_JSON below, then run: python3 debug_asin.py
"""

import json
import requests

TARGET_ASIN  = "B0795N39KL"
PROMOTION_ID = "34929ff7-d101-4d19-a8ca-3a14a46b5cfb"

COOKIES_JSON = """
PASTE YOUR COOKIE JSON HERE
"""

SELLER_CENTRAL = "https://sellercentral.amazon.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": f"{SELLER_CENTRAL}/coupons",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}


def main():
    session = requests.Session()
    session.headers.update(HEADERS)
    cookies = json.loads(COOKIES_JSON.strip())
    if isinstance(cookies, list):
        for c in cookies:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ".amazon.com"))
    else:
        session.cookies.update(cookies)

    # Step 1: get productSelectionId from detail
    print(f"Fetching detail for promotion: {PROMOTION_ID}")
    detail_resp = session.get(
        f"{SELLER_CENTRAL}/coupons/api/couponPromotion",
        params={"promotionId": PROMOTION_ID, "clientId": "LegacyCouponsUI"},
        timeout=20,
    )
    print(f"  Status: {detail_resp.status_code}")
    detail = detail_resp.json()
    psid = detail.get("productSelectionId")
    print(f"  productSelectionId: {psid}\n")

    # Step 2: call products endpoint
    print(f"Fetching products for productSelectionId: {psid}")
    prod_resp = session.get(
        f"{SELLER_CENTRAL}/coupons/api/couponPromotion/products",
        params={"productSelectionId": psid, "pageSize": 999, "clientId": "LegacyCouponsUI"},
        timeout=20,
    )
    print(f"  Status: {prod_resp.status_code}")
    data = prod_resp.json()
    products = data.get("couponPromotionProducts") or []
    print(f"  Total products returned: {len(products)}\n")

    # Step 3: find target ASIN
    target = [p for p in products if p.get("asin") == TARGET_ASIN]
    if not target:
        print(f"ASIN {TARGET_ASIN} NOT found.")
        print("ASINs in this coupon:", [p.get("asin") for p in products[:10]])
    else:
        print(f"Raw object for {TARGET_ASIN}:")
        print(json.dumps(target[0], indent=2))


if __name__ == "__main__":
    main()
