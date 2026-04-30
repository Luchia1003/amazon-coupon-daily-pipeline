# Amazon Seller Central Coupon Daily Pipeline Spec

## Objective

Build a fully automated daily Python pipeline that extracts Amazon Seller Central coupon data, enriches it with ASIN → SKU mapping, and loads the final dataset into Snowflake.

This pipeline runs once daily.

Final output table will be used for dashboard reporting and joined with AMAZON_MART.AMAZON_ORDER.

---

# Final Output Table

## Table Name

AMAZON_MART.AMAZON_COUPON_DAILY

---

# Primary Key

Use this combination as the unique row key:

LOAD_DATE + PROMOTION_ID + ASIN + SKU

This supports:

- Daily snapshots
- One promotion with multiple ASINs
- One ASIN with multiple SKUs
- One SKU appearing in multiple promotions

---

# Required Columns

## Snapshot Info

LOAD_DATE DATE

Date pipeline runs.

---

## Coupon Identity

PROMOTION_ID STRING  
TITLE STRING  
STATUS STRING  
PSSS_STATUS STRING  
NEEDS_ATTENTION BOOLEAN

---

## Coupon Dates

START_DATE TIMESTAMP  
END_DATE TIMESTAMP

---

## Product Info

ASIN STRING  
SKU STRING  
ASIN_COUNT NUMBER  
INVENTORY NUMBER  
PRICE NUMBER(18,2)

---

## Coupon Config

BUDGET NUMBER(18,2)  
BUDGET_TYPE STRING  
BUDGET_STATUS STRING  

DISCOUNT_TYPE STRING  
DISCOUNT_VALUE NUMBER(18,2)

CUSTOMER_SEGMENT STRING  
COUPON_TYPE STRING  
ONCE_PER_CUSTOMER BOOLEAN

---

## Coupon Metrics

BUDGET_SPENT NUMBER(18,2)  
BUDGET_UTILIZATION NUMBER(18,2)  
CLIP_COUNT NUMBER  
REDEMPTION_COUNT NUMBER  
SALES NUMBER(18,2)

---

## Coupon Fee

PARTICIPATION_FEE NUMBER(18,2)  
PERFORMANCE_FEE NUMBER(18,4)  
FEE_CAP NUMBER(18,2)  
FEE_CHARGED NUMBER(18,2)  
CURRENCY_CODE STRING

---

# Pipeline Logic

## Step 1 — Login Seller Central

Use Playwright.

Requirements:

- Persist session locally
- Reuse login cookies
- Avoid logging in daily if session valid

---

## Step 2 — Pull Coupon List Page API

Use internal coupon list endpoint discovered in browser network tab.

This returns all coupon summary rows.

Required fields:

PROMOTION_ID  
TITLE  
STATUS  
START_DATE  
END_DATE  
BUDGET  
DISCOUNT_VALUE  
CLIP_COUNT  
REDEMPTION_COUNT  
SALES  
BUDGET_SPENT

Handle pagination until all coupons are returned.

---

## Step 3 — Pull Coupon Detail API

For each PROMOTION_ID:

Call coupon detail endpoint.

Only required extra field:

PRODUCT_SELECTION_ID

---

## Step 4 — Pull Product API

For each PRODUCT_SELECTION_ID:

Call products endpoint.

Return:

ASIN  
TITLE  
INVENTORY  
PRICE

One promotion may have multiple ASIN rows.

---

## Step 5 — ASIN to SKU Mapping

Use Amazon Seller Central / Amazon internal product APIs to map ASIN → SKU.

---

## Correct Mapping Priority

1. Coupon Product API (if SKU exists in hidden fields)
2. Seller Central inventory / listing API endpoints
3. Category / catalog / listings page APIs discovered in browser Network tab
4. Existing internal inventory master tables
5. If still unavailable: SKU = NULL

---

## Goal

For every ASIN returned from coupon product API, obtain merchant SKU.

Final result:

ASIN → SKU

---

## Expected Reality

One ASIN may map to:

- One SKU
- Multiple SKUs (FBA / FBM / duplicate listings / bundles)

If multiple SKUs exist:

Create multiple rows.

Example:

ASIN = B07ABC123

maps to:

SKU_A  
SKU_B

Then output:

LOAD_DATE + PROMOTION_ID + ASIN + SKU_A  
LOAD_DATE + PROMOTION_ID + ASIN + SKU_B

LOAD_DATE + PROMOTION_ID + ASIN + SKU_B

---

## Recommended Implementation

After coupon product API returns ASIN list:

Run second lookup call:

for each ASIN:

GET SKU mapping from internal Amazon listing endpoint

Then merge into final dataframe.

---

## If SKU Cannot Be Found

Keep row with:

SKU = NULL

Still load into Snowflake.

Later mapping can be backfilled.


## Step 6 — Build Final Dataset

Each final row should represent:

LOAD_DATE + PROMOTION_ID + ASIN + SKU

Merge coupon metrics + product data + SKU mapping.

---

## Step 7 — Load to Snowflake

Target table:

RAW.AMAZON_COUPON_DAILY

Recommended method:

Python pandas dataframe + write_pandas()

Before insert:

Delete same LOAD_DATE rows first.

Example logic:

DELETE FROM RAW.AMAZON_COUPON_DAILY
WHERE LOAD_DATE = CURRENT_DATE;

Then append fresh rows.

---

# Schedule

Run once daily.

Recommended time:

08:15 AM Pacific Time

Use:

- GitHub Actions (recommended)
or
- Cron job
or
- Render scheduled worker

---

# Dashboard Join Logic

Join to order table:

AMAZON_MART.AMAZON_ORDER

Recommended join:

SKU = SALES_SKU

and

ORDER_DATE between START_DATE and END_DATE

---

# Future Dashboard Metrics

## Coupon ROI

SALES - Coupon Fees - Margin Impact

## Coupon by SKU

Sales / Orders / Profit by SKU

## Coupon by Promotion

Spend vs Revenue

## Coupon by Brand

Grouped by SKU brand family

## Active Coupons

Currently RUNNING promotions

---

# Engineering Requirements

## Python

Use modular structure:

main.py  
seller_login.py  
coupon_api.py  
snowflake_loader.py

## Error Handling

Retry failed requests.

Skip bad coupon rows.

Continue pipeline if one promotion fails.

## Logging

Print:

Coupons found  
Products found  
Rows loaded  
Runtime seconds

---

# Final Goal

Produce a clean daily fact table that allows instant reporting of:

- Which coupons drive sales
- Which coupons lose money
- Which SKUs benefit most
- True coupon ROI
- Full coupon performance history