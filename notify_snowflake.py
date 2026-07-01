#!/usr/bin/env python3
"""
Signal the Snowflake dashboard pipeline that this source's daily load is done.

Called as the final step of a GitHub Actions sync workflow, once all data steps
have succeeded. It records an arrival in DASHBOARD_DB.PIPELINE_SIGNAL via
DASHBOARD_DB.SP_PIPELINE_SIGNAL(<source>). When all three required sources
(AMAZON, SHOPIFY, SHIPPING) have signaled on the same UTC day, that last call
fires DASHBOARD_DB.SP_RUN_CORE_PIPELINE() once — so the dashboard tables are
rebuilt only after every GitHub load has landed, never on a fixed clock.

Usage:  python notify_snowflake.py <AMAZON|SHOPIFY|SHIPPING>

Reuses whatever Snowflake env vars the host repo already sets, accepting either
the SF_* or SNOWFLAKE_* naming convention.
"""
import os
import sys

import snowflake.connector
from cryptography.hazmat.primitives import serialization


def _env(*names, required=True):
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    if required:
        raise SystemExit(f"[notify_snowflake] missing env var (one of {names})")
    return None


def main():
    source = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("PIPELINE_SOURCE", "")).upper()
    if source not in ("AMAZON", "SHOPIFY", "SHIPPING", "COUPON"):
        raise SystemExit(f"[notify_snowflake] invalid source '{source}' (AMAZON|SHOPIFY|SHIPPING|COUPON)")

    pem = _env("SF_PRIVATE_KEY_PEM", "SNOWFLAKE_PRIVATE_KEY")
    p_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    pkb = p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    conn = snowflake.connector.connect(
        user=_env("SF_USER", "SNOWFLAKE_USER"),
        account=_env("SF_ACCOUNT", "SNOWFLAKE_ACCOUNT"),
        warehouse=_env("SF_WAREHOUSE", "SNOWFLAKE_WAREHOUSE"),
        role=_env("SF_ROLE", "SNOWFLAKE_ROLE"),
        database="SKU_PROFIT_PROJECT",
        schema="DASHBOARD_DB",
        private_key=pkb,
    )
    try:
        cur = conn.cursor()
        cur.execute("CALL DASHBOARD_DB.SP_PIPELINE_SIGNAL(%s)", (source,))
        row = cur.fetchone()
        print(f"[notify_snowflake] {source} -> {row[0] if row else '(no result)'}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
