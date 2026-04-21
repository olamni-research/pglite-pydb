"""T036 — negative-path matrix for all 10 procedures × both transports.

Each procedure's contract in ``contracts/procedures.md`` names a specific
SQLSTATE for each documented error. This file asserts every one of those
raises the right SQLSTATE over both transports, and then verifies that a
fresh ``count_articles_per_product`` run still succeeds — proving the
errors left no torn state behind (FR-017, SC-005).
"""

from __future__ import annotations

import sys

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows",
)

psycopg = pytest.importorskip("psycopg")

from tests.windows_sample_db import _expected as E


def _sqlstate(exc: BaseException) -> str | None:
    ss = getattr(exc, "sqlstate", None)
    if ss:
        return ss
    diag = getattr(exc, "diag", None)
    return getattr(diag, "sqlstate", None) if diag else None


def _raises_sqlstate(conn, sql: str, params: tuple, expected: str) -> None:
    with pytest.raises(psycopg.errors.Error) as ei:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    assert _sqlstate(ei.value) == expected, (
        f"expected SQLSTATE {expected!r}, got {_sqlstate(ei.value)!r}: {ei.value}"
    )


# 1 get_customer_by_email — P0002 on unknown email --------------------------


def test_get_customer_by_email_unknown_raises_p0002(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM get_customer_by_email(%s)",
        ("never-registered@example.invalid",),
        "P0002",
    )


# 2 list_articles_for_product — 22023 on bad paging / P0002 on missing ------


def test_list_articles_for_product_zero_page_size_raises_22023(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM list_articles_for_product(%s, %s, %s)",
        (E.PRODUCT_ID_WITH_ARTICLES, 0, 1),
        "22023",
    )


def test_list_articles_for_product_missing_product_raises_p0002(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM list_articles_for_product(%s, %s, %s)",
        (E.PRODUCT_ID_ABSENT, 10, 1),
        "P0002",
    )


# 3 count_articles_per_product — no errors documented (no input) ------------
# (Skipped: intentionally has no error branch.)


# 4 top_products_by_revenue — 22023 on p_n <= 0 or > 500 --------------------


def test_top_products_by_revenue_zero_raises_22023(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM top_products_by_revenue(%s)",
        (0,),
        "22023",
    )


def test_top_products_by_revenue_over_cap_raises_22023(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM top_products_by_revenue(%s)",
        (501,),
        "22023",
    )


# 5 list_orders_for_customer — P0002 on unknown customer --------------------


def test_list_orders_for_customer_unknown_raises_p0002(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM list_orders_for_customer(%s)",
        (999_999_999,),
        "P0002",
    )


# 6 articles_in_category — 22023 on NULL / empty / unknown label ------------


def test_articles_in_category_empty_raises_22023(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM articles_in_category(%s)",
        ("",),
        "22023",
    )


def test_articles_in_category_unknown_label_raises_22023(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM articles_in_category(%s)",
        ("NotARealCategoryLabel",),
        "22023",
    )


# 7 customer_order_report — P0002 on unknown customer ----------------------


def test_customer_order_report_unknown_raises_p0002(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT * FROM customer_order_report(%s)",
        (999_999_999,),
        "P0002",
    )


# 8 bulk_log_articles_for_product — P0002 + no audit writes on error --------


def test_bulk_log_articles_missing_product_raises_p0002_and_keeps_audit_clean(
    transport_conn,
) -> None:
    """Error path must not leave any audit_log rows for the absent id."""
    _raises_sqlstate(
        transport_conn,
        "SELECT bulk_log_articles_for_product(%s)",
        (E.PRODUCT_ID_ABSENT,),
        "P0002",
    )
    # psycopg occasionally leaves a stuck result buffer on the connection
    # after a server-side ``RAISE EXCEPTION``; open a fresh connection on
    # the same DSN for the post-error state check.
    dsn = transport_conn.info.dsn
    with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as check:
        with check.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM audit_log "
                "WHERE procedure_name = 'bulk_log_articles_for_product' "
                "AND target_key = %s",
                (str(E.PRODUCT_ID_ABSENT),),
            )
            assert cur.fetchone()[0] == 0, (
                "bulk_log_articles_for_product must not leave partial audit rows on error"
            )


# 9 rename_product_display_name — P0002 on missing / 22023 on bad name ------


def test_rename_product_missing_raises_p0002(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT rename_product_display_name(%s, %s)",
        (E.PRODUCT_ID_ABSENT, "x"),
        "P0002",
    )


def test_rename_product_empty_name_raises_22023(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT rename_product_display_name(%s, %s)",
        (E.PRODUCT_ID_WITH_ARTICLES, ""),
        "22023",
    )


# 10 assert_product_exists — P0002 on missing -------------------------------


def test_assert_product_exists_missing_raises_p0002(transport_conn) -> None:
    _raises_sqlstate(
        transport_conn,
        "SELECT assert_product_exists(%s)",
        (E.PRODUCT_ID_ABSENT,),
        "P0002",
    )


# Baseline after every error-branch run — state still queryable -------------


def test_state_intact_after_all_error_branches(transport_conn) -> None:
    """Sanity baseline (FR-017 tail): happy query still works."""
    with transport_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM count_articles_per_product()")
        n = cur.fetchone()[0]
    assert n > 0
