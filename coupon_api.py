import os
import logging
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.async_api import Page

logger = logging.getLogger(__name__)

SELLER_CENTRAL = "https://sellercentral.amazon.com"


def _seller_id() -> str:
    return os.environ["AMAZON_SELLER_ID"]


def _marketplace_id() -> str:
    return os.environ.get("AMAZON_MARKETPLACE_ID", "ATVPDKIKX0DER")


# ---------------------------------------------------------------------------
# Step 2 — Coupon List (paginated)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def fetch_coupon_list_page(page: Page, page_index: int = 0, page_size: int = 50) -> dict:
    """
    Call the internal Seller Central coupon list API.
    Returns raw JSON response for one page.
    """
    url = (
        f"{SELLER_CENTRAL}/coupons/api/coupon/list"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
        f"&pageIndex={page_index}"
        f"&pageSize={page_size}"
        f"&sortBy=START_DATE"
        f"&sortOrder=DESC"
    )
    response = await page.evaluate(
        """async (url) => {
            const resp = await fetch(url, {credentials: 'include'});
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return await resp.json();
        }""",
        url,
    )
    return response


async def fetch_all_coupons(page: Page) -> list[dict]:
    """
    Paginate through the coupon list API and collect all coupon rows.
    Returns a flat list of coupon summary dicts.
    """
    all_coupons = []
    page_index = 0
    page_size = 50

    while True:
        logger.info(f"Fetching coupon list page {page_index}...")
        try:
            data = await fetch_coupon_list_page(page, page_index=page_index, page_size=page_size)
        except Exception as e:
            logger.error(f"Failed to fetch coupon list page {page_index}: {e}")
            break

        coupons = data.get("coupons") or data.get("couponList") or data.get("items") or []
        if not coupons:
            break

        all_coupons.extend(coupons)
        logger.info(f"  Page {page_index}: {len(coupons)} coupons (total so far: {len(all_coupons)})")

        total = data.get("totalCount") or data.get("total") or 0
        if len(all_coupons) >= total or len(coupons) < page_size:
            break

        page_index += 1

    logger.info(f"Coupon list complete: {len(all_coupons)} coupons found.")
    return all_coupons


def parse_coupon_summary(raw: dict) -> dict:
    """Normalise a raw coupon list row into our standard field names."""
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
async def fetch_coupon_detail(page: Page, promotion_id: str) -> dict:
    """
    Fetch coupon detail for a single PROMOTION_ID.
    Returns PRODUCT_SELECTION_ID and any extra detail fields.
    """
    url = (
        f"{SELLER_CENTRAL}/coupons/api/coupon/{promotion_id}"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
    )
    response = await page.evaluate(
        """async (url) => {
            const resp = await fetch(url, {credentials: 'include'});
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return await resp.json();
        }""",
        url,
    )
    return response


def parse_coupon_detail(raw: dict) -> dict:
    """Extract PRODUCT_SELECTION_ID and supplemental fields from detail response."""
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
# Step 4 — Product API (ASIN list per coupon)
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def fetch_products(page: Page, product_selection_id: str) -> list[dict]:
    """
    Fetch all products for a given PRODUCT_SELECTION_ID.
    Returns list of dicts with ASIN, TITLE, INVENTORY, PRICE.
    """
    url = (
        f"{SELLER_CENTRAL}/coupons/api/product-selection/{product_selection_id}/products"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
    )
    response = await page.evaluate(
        """async (url) => {
            const resp = await fetch(url, {credentials: 'include'});
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return await resp.json();
        }""",
        url,
    )
    products = response.get("products") or response.get("items") or response if isinstance(response, list) else []
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
async def fetch_sku_for_asin(page: Page, asin: str) -> list[str]:
    """
    Look up merchant SKU(s) for a given ASIN via the Seller Central
    listings/inventory endpoint.

    Priority:
      1. Catalog / listing endpoint
      2. Inventory summary endpoint
      Falls back to [None] if nothing found.
    """
    # Try catalog listings endpoint first
    skus = await _sku_from_catalog(page, asin)
    if skus:
        return skus

    # Fallback: inventory summary
    skus = await _sku_from_inventory(page, asin)
    if skus:
        return skus

    return [None]


async def _sku_from_catalog(page: Page, asin: str) -> list[str]:
    url = (
        f"{SELLER_CENTRAL}/inventory/api/ownedAsins/{asin}"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
    )
    try:
        data = await page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {credentials: 'include'});
                if (!resp.ok) return null;
                return await resp.json();
            }""",
            url,
        )
        if not data:
            return []
        items = data.get("listings") or data.get("items") or []
        return [i.get("sku") or i.get("merchantSku") for i in items if i.get("sku") or i.get("merchantSku")]
    except Exception as e:
        logger.debug(f"Catalog lookup failed for {asin}: {e}")
        return []


async def _sku_from_inventory(page: Page, asin: str) -> list[str]:
    url = (
        f"{SELLER_CENTRAL}/inventory/api/inventorySummaries"
        f"?sellerId={_seller_id()}"
        f"&marketplaceId={_marketplace_id()}"
        f"&asin={asin}"
    )
    try:
        data = await page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {credentials: 'include'});
                if (!resp.ok) return null;
                return await resp.json();
            }""",
            url,
        )
        if not data:
            return []
        summaries = (
            data.get("inventorySummaries")
            or data.get("payload", {}).get("inventorySummaries")
            or []
        )
        return [s.get("sellerSku") or s.get("sku") for s in summaries if s.get("sellerSku") or s.get("sku")]
    except Exception as e:
        logger.debug(f"Inventory lookup failed for {asin}: {e}")
        return []
