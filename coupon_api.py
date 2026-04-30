import os
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

SELLER_CENTRAL = "https://sellercentral.amazon.com"


def _seller_id() -> str:
    return os.environ["AMAZON_SELLER_ID"]


def _marketplace_id() -> str:
    return os.environ.get("AMAZON_MARKETPLACE_ID", "ATVPDKIKX0DER")


def _get(session: requests.Session, url: str) -> dict:
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Step 2 — Coupon List (paginated)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_coupon_list_page(session: requests.Session, page_index: int = 0, page_size: int = 50) -> dict:
    url = (
        f"{SELLER_CENTRAL}/coupons/api/coupon/list"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
        f"&pageIndex={page_index}"
        f"&pageSize={page_size}"
        f"&sortBy=START_DATE"
        f"&sortOrder=DESC"
    )
    return _get(session, url)


def fetch_all_coupons(session: requests.Session) -> list[dict]:
    """Paginate through coupon list and return all coupon rows."""
    all_coupons = []
    page_index = 0
    page_size = 50

    while True:
        logger.info(f"Fetching coupon list page {page_index}...")
        try:
            data = fetch_coupon_list_page(session, page_index=page_index, page_size=page_size)
        except Exception as e:
            logger.error(f"Failed to fetch coupon list page {page_index}: {e}")
            break

        coupons = data.get("coupons") or data.get("couponList") or data.get("items") or []
        if not coupons:
            break

        all_coupons.extend(coupons)
        logger.info(f"  Page {page_index}: {len(coupons)} coupons (total: {len(all_coupons)})")

        total = data.get("totalCount") or data.get("total") or 0
        if len(all_coupons) >= total or len(coupons) < page_size:
            break

        page_index += 1

    logger.info(f"Coupon list complete: {len(all_coupons)} coupons found.")
    return all_coupons


def parse_coupon_summary(raw: dict) -> dict:
    return {
        "PROMOTION_ID": raw.get("promotionId") or raw.get("couponId") or raw.get("id"),
        "TITLE": raw.get("title") or raw.get("name"),
        "STATUS": raw.get("status"),
        "PSSS_STATUS": raw.get("psssStatus") or raw.get("approvalStatus"),
        "NEEDS_ATTENTION": bool(raw.get("needsAttention", False)),
        "START_DATE": raw.get("startDate") or raw.get("startTime"),
        "END_DATE": raw.get("endDate") or raw.get("endTime"),
        "BUDGET": raw.get("budget") or raw.get("totalBudget"),
        "BUDGET_TYPE": raw.get("budgetType"),
        "BUDGET_STATUS": raw.get("budgetStatus"),
        "DISCOUNT_TYPE": raw.get("discountType"),
        "DISCOUNT_VALUE": raw.get("discountValue") or raw.get("discount"),
        "CUSTOMER_SEGMENT": raw.get("customerSegment"),
        "COUPON_TYPE": raw.get("couponType"),
        "ONCE_PER_CUSTOMER": bool(raw.get("oncePerCustomer", False)),
        "BUDGET_SPENT": raw.get("budgetSpent") or raw.get("spentBudget"),
        "BUDGET_UTILIZATION": raw.get("budgetUtilization"),
        "CLIP_COUNT": raw.get("clipCount") or raw.get("clippedCount"),
        "REDEMPTION_COUNT": raw.get("redemptionCount") or raw.get("redeemedCount"),
        "SALES": raw.get("sales") or raw.get("totalSales"),
    }


# ---------------------------------------------------------------------------
# Step 3 — Coupon Detail
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_coupon_detail(session: requests.Session, promotion_id: str) -> dict:
    url = (
        f"{SELLER_CENTRAL}/coupons/api/coupon/{promotion_id}"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
    )
    return _get(session, url)


def parse_coupon_detail(raw: dict) -> dict:
    coupon = raw.get("coupon") or raw.get("data") or raw
    return {
        "PRODUCT_SELECTION_ID": (
            coupon.get("productSelectionId")
            or coupon.get("productSelection", {}).get("id")
        ),
        "PARTICIPATION_FEE": coupon.get("participationFee"),
        "PERFORMANCE_FEE": coupon.get("performanceFee"),
        "FEE_CAP": coupon.get("feeCap"),
        "FEE_CHARGED": coupon.get("feeCharged"),
        "CURRENCY_CODE": coupon.get("currencyCode") or coupon.get("currency"),
        "ASIN_COUNT": coupon.get("asinCount") or coupon.get("productCount"),
    }


# ---------------------------------------------------------------------------
# Step 4 — Product API
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def fetch_products(session: requests.Session, product_selection_id: str) -> list[dict]:
    url = (
        f"{SELLER_CENTRAL}/coupons/api/product-selection/{product_selection_id}/products"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
    )
    response = _get(session, url)
    products = (
        response.get("products")
        or response.get("items")
        or (response if isinstance(response, list) else [])
    )
    return [_parse_product(p) for p in products]


def _parse_product(raw: dict) -> dict:
    return {
        "ASIN": raw.get("asin"),
        "SKU_FROM_PRODUCT_API": raw.get("sku") or raw.get("merchantSku"),
        "TITLE": raw.get("title") or raw.get("name"),
        "INVENTORY": raw.get("inventory") or raw.get("quantity"),
        "PRICE": raw.get("price") or raw.get("listPrice"),
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
    """
    Lookup merchant SKU(s) for a given ASIN.
    Tries catalog endpoint first, then inventory summary.
    Returns [None] if nothing found.
    """
    skus = _sku_from_catalog(session, asin)
    if skus:
        return skus

    skus = _sku_from_inventory(session, asin)
    if skus:
        return skus

    return [None]


def _sku_from_catalog(session: requests.Session, asin: str) -> list[str]:
    url = (
        f"{SELLER_CENTRAL}/inventory/api/ownedAsins/{asin}"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
    )
    try:
        data = _get(session, url)
        items = data.get("listings") or data.get("items") or []
        return [i.get("sku") or i.get("merchantSku") for i in items if i.get("sku") or i.get("merchantSku")]
    except Exception as e:
        logger.debug(f"Catalog SKU lookup failed for {asin}: {e}")
        return []


def _sku_from_inventory(session: requests.Session, asin: str) -> list[str]:
    url = (
        f"{SELLER_CENTRAL}/inventory/api/inventorySummaries"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
        f"&asin={asin}"
    )
    try:
        data = _get(session, url)
        summaries = (
            data.get("inventorySummaries")
            or data.get("payload", {}).get("inventorySummaries")
            or []
        )
        return [s.get("sellerSku") or s.get("sku") for s in summaries if s.get("sellerSku") or s.get("sku")]
    except Exception as e:
        logger.debug(f"Inventory SKU lookup failed for {asin}: {e}")
        return []
