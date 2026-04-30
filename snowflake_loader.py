import os
import logging
import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)

TARGET_TABLE = "AMAZON_COUPON_DAILY"
TARGET_SCHEMA = "RAW"

COLUMN_ORDER = [
    "LOAD_DATE",
    "PROMOTION_ID",
    "TITLE",
    "STATUS",
    "PSSS_STATUS",
    "NEEDS_ATTENTION",
    "START_DATE",
    "END_DATE",
    "ASIN",
    "SKU",
    "ASIN_COUNT",
    "INVENTORY",
    "PRICE",
    "BUDGET",
    "BUDGET_TYPE",
    "BUDGET_STATUS",
    "DISCOUNT_TYPE",
    "DISCOUNT_VALUE",
    "CUSTOMER_SEGMENT",
    "COUPON_TYPE",
    "ONCE_PER_CUSTOMER",
    "BUDGET_SPENT",
    "BUDGET_UTILIZATION",
    "CLIP_COUNT",
    "REDEMPTION_COUNT",
    "SALES",
    "PARTICIPATION_FEE",
    "PERFORMANCE_FEE",
    "FEE_CAP",
    "FEE_CHARGED",
    "CURRENCY_CODE",
]

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TARGET_SCHEMA}.{TARGET_TABLE} (
    LOAD_DATE           DATE,
    PROMOTION_ID        VARCHAR,
    TITLE               VARCHAR,
    STATUS              VARCHAR,
    PSSS_STATUS         VARCHAR,
    NEEDS_ATTENTION     BOOLEAN,
    START_DATE          TIMESTAMP_NTZ,
    END_DATE            TIMESTAMP_NTZ,
    ASIN                VARCHAR,
    SKU                 VARCHAR,
    ASIN_COUNT          NUMBER,
    INVENTORY           NUMBER,
    PRICE               NUMBER(18,2),
    BUDGET              NUMBER(18,2),
    BUDGET_TYPE         VARCHAR,
    BUDGET_STATUS       VARCHAR,
    DISCOUNT_TYPE       VARCHAR,
    DISCOUNT_VALUE      NUMBER(18,2),
    CUSTOMER_SEGMENT    VARCHAR,
    COUPON_TYPE         VARCHAR,
    ONCE_PER_CUSTOMER   BOOLEAN,
    BUDGET_SPENT        NUMBER(18,2),
    BUDGET_UTILIZATION  NUMBER(18,2),
    CLIP_COUNT          NUMBER,
    REDEMPTION_COUNT    NUMBER,
    SALES               NUMBER(18,2),
    PARTICIPATION_FEE   NUMBER(18,2),
    PERFORMANCE_FEE     NUMBER(18,4),
    FEE_CAP             NUMBER(18,2),
    FEE_CHARGED         NUMBER(18,2),
    CURRENCY_CODE       VARCHAR
)
"""


def _load_private_key() -> bytes:
    """Load RSA private key and return DER-encoded bytes for Snowflake."""
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "/Users/luchia/rsa_private_key.pem")
    key_passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")

    with open(key_path, "rb") as f:
        private_key_pem = f.read()

    p_key = serialization.load_pem_private_key(
        private_key_pem,
        password=key_passphrase.encode() if key_passphrase else None,
        backend=default_backend(),
    )
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _get_connection() -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        user=os.environ["SNOWFLAKE_USER"],
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=TARGET_SCHEMA,
        role=os.environ["SNOWFLAKE_ROLE"],
        private_key=_load_private_key(),
    )


def ensure_table_exists(conn: snowflake.connector.SnowflakeConnection):
    """Create target table if it does not already exist."""
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    logger.info(f"Table {TARGET_SCHEMA}.{TARGET_TABLE} is ready.")


def delete_today_rows(conn: snowflake.connector.SnowflakeConnection, load_date: str):
    """Delete existing rows for today's LOAD_DATE before re-inserting."""
    sql = f"DELETE FROM {TARGET_SCHEMA}.{TARGET_TABLE} WHERE LOAD_DATE = '{load_date}'"
    with conn.cursor() as cur:
        cur.execute(sql)
        deleted = cur.rowcount
    logger.info(f"Deleted {deleted} existing rows for LOAD_DATE={load_date}.")


def load_to_snowflake(df: pd.DataFrame, load_date: str) -> int:
    """
    Load the final dataframe into Snowflake.
    1. Ensures table exists.
    2. Deletes today's rows.
    3. Appends new rows via write_pandas().
    Returns the number of rows inserted.
    """
    if df.empty:
        logger.warning("DataFrame is empty — nothing to load.")
        return 0

    # Ensure correct column order; add missing columns as None
    for col in COLUMN_ORDER:
        if col not in df.columns:
            df[col] = None
    df = df[COLUMN_ORDER].copy()

    # Coerce all NUMBER columns — any dict/list that snuck in becomes NaN
    NUMBER_COLS = [
        "ASIN_COUNT", "INVENTORY", "PRICE", "BUDGET", "DISCOUNT_VALUE",
        "BUDGET_SPENT", "BUDGET_UTILIZATION", "CLIP_COUNT", "REDEMPTION_COUNT",
        "SALES", "PARTICIPATION_FEE", "PERFORMANCE_FEE", "FEE_CAP", "FEE_CHARGED",
    ]
    for col in NUMBER_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Strip timezone from datetime columns (Snowflake TIMESTAMP_NTZ expects tz-naive)
    for col in ("START_DATE", "END_DATE"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_localize(None)

    conn = _get_connection()
    try:
        ensure_table_exists(conn)
        delete_today_rows(conn, load_date)

        success, num_chunks, num_rows, output = write_pandas(
            conn=conn,
            df=df,
            table_name=TARGET_TABLE,
            schema=TARGET_SCHEMA,
            database=os.environ["SNOWFLAKE_DATABASE"],
            auto_create_table=False,
            overwrite=False,
            use_logical_type=True,
        )

        if success:
            logger.info(f"Loaded {num_rows} rows into {TARGET_SCHEMA}.{TARGET_TABLE} ({num_chunks} chunk(s)).")
        else:
            logger.error(f"write_pandas returned failure. Output: {output}")

        return num_rows
    finally:
        conn.close()
