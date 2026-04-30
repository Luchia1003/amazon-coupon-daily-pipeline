import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

SELLER_CENTRAL = "https://sellercentral.amazon.com"
CLIENT_ID = "LegacyCouponsUI"


def _get(session: requests.Session, url: str, params: dict = None) -> dict:
    headers = {
        "Referer": f"{SELLER_CENTRAL}/coupons",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/plain, */*",
    }
    resp = session.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Step 2 — Coupon List (paginated)
# Real endpoint: /coupons/api/getCouponPromotions
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_coupon_list_page(session: requests.Session, skip: int = 0, page_size: int = 50) -> dict:
    return _get(
        session,
        f"{SELLER_CENTRAL}/coupons/api/getCouponPromotions",
        params={
            "paginationSize": page_size,
            "paginationSkip": skip,
            "clientId": CLIENT_ID,
        },
    )


def fetch_all_coupons(session: requests.Session) -> list[dict]:
    """Paginate through coupon list and return all coupon rows."""
    all_coupons = []
    skip = 0
    page_size = 50

    while True:
        logger.info(f"Fetching coupons (skip={skip})...")
        try:
            data = fetch_coupon_list_page(session, skip=skip, page_size=page_size)
        except Exception as e:
            logger.error(f"Failed to fetch coupon list (skip={skip}): {e}")
            break

        coupons = data.get("promotionSearchResultList") or []
        if not coupons:
            break

        all_coupons.extend(coupons)
        logger.info(f"  Got {len(coupons)} coupons (total so far: {len(all_coupons)})")

        total = data.get("promotionTotalCount") or 0
        if len(all_coupons) >= total or len(coupons) < page_size:
            break

        skip += page_size

    logger.info(f"Coupon list complete: {len(all_coupons)} coupons found.")
    return all_coupons


def parse_coupon_summary(raw: dict) -> dict:
    """Map list API fields → our standard column names."""
    metrics = raw.get("couponMetrics") or {}
    return {
        "PROMOTION_ID":       raw.get("obfuscatedPromotionId") or raw.get("promotionId"),
        "TITLE":              raw.get("title") or raw.get("name"),
        "STATUS":             raw.get("status"),
        "PSSS_STATUS":        raw.get("psssStatus") or raw.get("approvalStatus"),
        "NEEDS_ATTENTION":    bool(raw.get("needsAttention", False)),
        "START_DATE":         raw.get("startDate"),
        "END_DATE":           raw.get("endDate"),
        "BUDGET":             raw.get("budget") or raw.get("totalBudget"),
        "BUDGET_TYPE":        raw.get("budgetType"),
        "BUDGET_STATUS":      raw.get("budgetStatus"),
        "DISCOUNT_TYPE":      raw.get("discountType"),
        "DISCOUNT_VALUE":     raw.get("discountValue") or raw.get("discount"),
        "CUSTOMER_SEGMENT":   raw.get("customerSegment"),
        "COUPON_TYPE":        raw.get("couponType"),
        "ONCE_PER_CUSTOMER":  bool(raw.get("oncePerCustomer", False)),
        "ASIN_COUNT":         raw.get("asinCount"),
        # couponMetrics is a nested object
        "BUDGET_SPENT":       metrics.get("budgetSpent"),
        "BUDGET_UTILIZATION": metrics.get("budgetUtilization"),
        "CLIP_COUNT":         metrics.get("clipCount"),
        "REDEMPTION_COUNT":   metrics.get("redemptionCount"),
        "SALES":              metrics.get("sales") or metrics.get("totalSales"),
        # productSelectionId may already be in list response
        "_PRODUCT_SELECTION_ID": raw.get("productSelectionId"),
    }


# ---------------------------------------------------------------------------
# Step 3 — Coupon Detail (only needed if productSelectionId not in list)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_coupon_detail(session: requests.Session, promotion_id: str) -> dict:
    return _get(
        session,
        f"{SELLER_CENTRAL}/coupons/api/getCouponPromotion",
        params={"promotionId": promotion_id, "clientId": CLIENT_ID},
    )


def parse_coupon_detail(raw: dict) -> dict:
    coupon = raw.get("promotionDetail") or raw.get("coupon") or raw.get("data") or raw
    return {
        "PRODUCT_SELECTION_ID": (
            coupon.get("productSelectionId")
            or coupon.get("productSelection", {}).get("id")
        ),
        "PARTICIPATION_FEE": coupon.get("participationFee"),
        "PERFORMANCE_FEE":   coupon.get("performanceFee"),
        "FEE_CAP":           coupon.get("feeCap"),
        "FEE_CHARGED":       coupon.get("feeCharged"),
        "CURRENCY_CODE":     coupon.get("currencyCode") or coupon.get("currency"),
        "ASIN_COUNT":        coupon.get("asinCount") or coupon.get("productCount"),
    }


# ---------------------------------------------------------------------------
# Step 4 — Products API
# Real endpoint: /coupons/api/couponPromotion/products
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_products(session: requests.Session, product_selection_id: str) -> list[dict]:
    response = _get(
        session,
        f"{SELLER_CENTRAL}/coupons/api/couponPromotion/products",
        params={
            "productSelectionId": product_selection_id,
            "pageSize": 999,
            "clientId": CLIENT_ID,
        },
    )
    products = (
        response.get("products")
        or response.get("items")
        or (response if isinstance(response, list) else [])
    )
    return [_parse_product(p) for p in products]


def _parse_product(raw: dict) -> dict:
    return {
        "ASIN":               raw.get("asin"),
        "SKU_FROM_PRODUCT_API": raw.get("sku") or raw.get("merchantSku"),
        "TITLE":              raw.get("title") or raw.get("name"),
        "INVENTORY":          raw.get("inventory") or raw.get("quantity"),
        "PRICE":              raw.get("price") or raw.get("listPrice"),
    }


# ---------------------------------------------------------------------------
# Step 5 — ASIN → SKU Mapping
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_sku_for_asin(session: requests.Session, asin: str) -> list[str]:
    """Try catalog then inventory endpoint. Returns [None] if not found."""
    skus = _sku_from_catalog(session, asin)
    if skus:
        return skus
    skus = _sku_from_inventory(session, asin)
    if skus:
        return skus
    return [None]


def _sku_from_catalog(session: requests.Session, asin: str) -> list[str]:
    try:
        data = _get(
            session,
            f"{SELLER_CENTRAL}/inventory/api/ownedAsins/{asin}",
        )
        items = data.get("listings") or data.get("items") or []
        return [i.get("sku") or i.get("merchantSku") for i in items if i.get("sku") or i.get("merchantSku")]
    except Exception as e:
        logger.debug(f"Catalog SKU lookup failed for {asin}: {e}")
        return []


def _sku_from_inventory(session: requests.Session, asin: str) -> list[str]:
    try:
        data = _get(
            session,
            f"{SELLER_CENTRAL}/inventory/api/inventorySummaries",
            params={"asin": asin},
        )
        summaries = (
            data.get("inventorySummaries")
            or data.get("payload", {}).get("inventorySummaries")
            or []
        )
        return [s.get("sellerSku") or s.get("sku") for s in summaries if s.get("sellerSku") or s.get("sku")]
    except Exception as e:
        logger.debug(f"Inventory SKU lookup failed for {asin}: {e}")
        return []
