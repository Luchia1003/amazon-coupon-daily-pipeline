"""
Amazon Seller Central Coupon Daily Pipeline
============================================
Runs once daily:
  1. Login to Seller Central via requests (reuse session cookies if valid)
  2. Pull all coupons (paginated)
  3. Pull coupon detail  → PRODUCT_SELECTION_ID
  4. Pull product list   → ASIN(s) per coupon
  5. Map ASIN → SKU(s)
  6. Build final DataFrame
  7. Load to Snowflake RAW.AMAZON_COUPON_DAILY
"""

import logging
import time
from datetime import date

import pandas as pd
from dotenv import load_dotenv

from seller_login import get_session
from coupon_api import (
    fetch_all_coupons,
    parse_coupon_summary,
    fetch_coupon_detail,
    parse_coupon_detail,
    fetch_products,
    fetch_sku_for_asin,
)
from snowflake_loader import load_to_snowflake

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pipeline")


def run_pipeline():
    start_ts = time.time()
    load_date = date.today().isoformat()
    logger.info(f"=== Pipeline started | LOAD_DATE={load_date} ===")

    session = get_session()

    # ------------------------------------------------------------------
    # Step 2: Pull all coupons
    # ------------------------------------------------------------------
    raw_coupons = fetch_all_coupons(session)
    logger.info(f"Coupons found: {len(raw_coupons)}")

    if not raw_coupons:
        logger.warning("No coupons returned — pipeline exiting early.")
        return

    summaries = [parse_coupon_summary(c) for c in raw_coupons]

    # ------------------------------------------------------------------
    # Step 3 + 4 + 5: Detail → Products → SKU mapping
    # ------------------------------------------------------------------
    final_rows = []
    products_found_total = 0

    for summary in summaries:
        promotion_id = summary["PROMOTION_ID"]
        if not promotion_id:
            logger.warning("Skipping coupon row with no PROMOTION_ID.")
            continue

        # Step 3 — Coupon detail
        try:
            raw_detail = fetch_coupon_detail(session, promotion_id)
            detail = parse_coupon_detail(raw_detail)
        except Exception as e:
            logger.error(f"[{promotion_id}] Detail fetch failed: {e} — skipping.")
            continue

        product_selection_id = detail.get("PRODUCT_SELECTION_ID")
        if not product_selection_id:
            logger.warning(f"[{promotion_id}] No PRODUCT_SELECTION_ID — creating null-product row.")
            row = {**summary, **detail, "ASIN": None, "SKU": None,
                   "INVENTORY": None, "PRICE": None, "LOAD_DATE": load_date}
            final_rows.append(row)
            continue

        # Step 4 — Products
        try:
            products = fetch_products(session, product_selection_id)
        except Exception as e:
            logger.error(f"[{promotion_id}] Product fetch failed: {e} — skipping.")
            continue

        products_found_total += len(products)

        for product in products:
            asin = product.get("ASIN")

            # Step 5 — SKU mapping (Priority 1: already in product API)
            sku_from_product = product.get("SKU_FROM_PRODUCT_API")
            if sku_from_product:
                skus = [sku_from_product]
            else:
                try:
                    skus = fetch_sku_for_asin(session, asin) if asin else [None]
                except Exception as e:
                    logger.warning(f"[{promotion_id}] SKU lookup failed for ASIN={asin}: {e}")
                    skus = [None]

            # Step 6 — One row per (PROMOTION_ID, ASIN, SKU)
            for sku in skus:
                row = {
                    **summary,
                    **detail,
                    "LOAD_DATE": load_date,
                    "ASIN": asin,
                    "SKU": sku,
                    "INVENTORY": product.get("INVENTORY"),
                    "PRICE": product.get("PRICE"),
                }
                final_rows.append(row)

    logger.info(f"Products found: {products_found_total}")
    logger.info(f"Total rows built: {len(final_rows)}")

    # ------------------------------------------------------------------
    # Step 7: Load to Snowflake
    # ------------------------------------------------------------------
    df = pd.DataFrame(final_rows)

    for ts_col in ("START_DATE", "END_DATE"):
        if ts_col in df.columns:
            df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce", utc=True)

    rows_loaded = load_to_snowflake(df, load_date)

    elapsed = round(time.time() - start_ts, 1)
    logger.info(f"Rows loaded: {rows_loaded}")
    logger.info(f"Runtime: {elapsed}s")
    logger.info("=== Pipeline complete ===")


if __name__ == "__main__":
    run_pipeline()
