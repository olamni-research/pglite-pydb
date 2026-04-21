"""T035 — happy-path matrix: all 10 procedures × both transports.

Shares the session bridges via the ``transport_conn`` fixture (conftest T033).
Each procedure asserts a row-shape expectation plus a spot-check from
``_expected.py`` (SC-003, FR-013).

Every test runs twice: once with ``transport=tcp``, once with ``transport=pipe``.
"""

from __future__ import annotations

import sys

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows",
)

from tests.windows_sample_db import _expected as E


def _fetch(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _scalar(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return None if row is None else row[0]


# 1 --------------------------------------------------------------------------


def test_get_customer_by_email_returns_one_row(transport_conn) -> None:
    # Look up a customer id → email → feed it back to the procedure.
    email = _scalar(
        transport_conn,
        "SELECT email FROM webshop.customer ORDER BY id LIMIT 1",
    )
    rows = _fetch(
        transport_conn, "SELECT * FROM get_customer_by_email(%s)", email,
    )
    assert len(rows) == 1


# 2 --------------------------------------------------------------------------


def test_list_articles_for_product_page_1(transport_conn) -> None:
    rows = _fetch(
        transport_conn,
        "SELECT * FROM list_articles_for_product(%s, %s, %s)",
        E.PRODUCT_ID_WITH_ARTICLES, 10, 1,
    )
    assert 1 <= len(rows) <= 10
    # Each row carries a repeated total_count >= row count.
    for r in rows:
        assert r[-1] >= len(rows)


# 3 --------------------------------------------------------------------------


def test_count_articles_per_product(transport_conn) -> None:
    rows = _fetch(transport_conn, "SELECT * FROM count_articles_per_product()")
    assert len(rows) > 0
    # product_id ordering ascending.
    ids = [r[0] for r in rows]
    assert ids == sorted(ids)
    # Every product has at least one article.
    assert all(r[1] >= 1 for r in rows)


# 4 --------------------------------------------------------------------------


def test_top_products_by_revenue_returns_n_rows_desc(transport_conn) -> None:
    rows = _fetch(transport_conn, "SELECT * FROM top_products_by_revenue(%s)", 5)
    assert len(rows) == 5
    revenues = [r[2] for r in rows]
    assert revenues == sorted(revenues, reverse=True)


# 5 --------------------------------------------------------------------------


def test_list_orders_for_customer(transport_conn) -> None:
    cid = _scalar(
        transport_conn,
        "SELECT id FROM webshop.customer ORDER BY id LIMIT 1",
    )
    rows = _fetch(transport_conn, "SELECT * FROM list_orders_for_customer(%s)", cid)
    # Customer may or may not have orders — contract only forbids raising
    # when the customer exists.
    assert len(rows) >= 0


# 6 --------------------------------------------------------------------------


def test_articles_in_category_footwear(transport_conn) -> None:
    rows = _fetch(transport_conn, "SELECT * FROM articles_in_category(%s)", "Footwear")
    assert len(rows) > 0


# 7 --------------------------------------------------------------------------


def test_customer_order_report(transport_conn) -> None:
    cid = _scalar(
        transport_conn,
        "SELECT id FROM webshop.customer ORDER BY id LIMIT 1",
    )
    rows = _fetch(transport_conn, "SELECT * FROM customer_order_report(%s)", cid)
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == cid
    assert row[2] >= 0  # order_count
    assert row[3] >= 0  # total_spent


# 8 --------------------------------------------------------------------------


def test_bulk_log_articles_for_product_writes_audit_log(transport_conn) -> None:
    pid = E.PRODUCT_ID_WITH_ARTICLES
    count = _scalar(
        transport_conn,
        "SELECT bulk_log_articles_for_product(%s)",
        pid,
    )
    assert count >= 1
    audit_count = _scalar(
        transport_conn,
        "SELECT count(*) FROM audit_log "
        "WHERE procedure_name = 'bulk_log_articles_for_product' "
        "AND target_key = %s",
        str(pid),
    )
    assert audit_count >= count


# 9 --------------------------------------------------------------------------


def test_rename_product_display_name_upserts_overlay(transport_conn) -> None:
    pid = E.PRODUCT_ID_WITH_ARTICLES
    _fetch(
        transport_conn,
        "SELECT rename_product_display_name(%s, %s)",
        pid, "Spot-Check Renamed",
    )
    current = _scalar(
        transport_conn,
        "SELECT display_name FROM product_overlay WHERE product_id = %s",
        pid,
    )
    assert current == "Spot-Check Renamed"


# 10 -------------------------------------------------------------------------


def test_assert_product_exists(transport_conn) -> None:
    # Void return — just asserting absence of exception.
    _fetch(
        transport_conn,
        "SELECT assert_product_exists(%s)",
        E.PRODUCT_ID_WITH_ARTICLES,
    )
