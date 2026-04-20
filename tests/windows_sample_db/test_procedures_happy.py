"""T019 — end-to-end procedure smoke over TCP (TDD, pre-T021).

Two representative procedures prove the full wiring (bridge → psycopg →
installed function → result set) ahead of the full 10×2 matrix in T035:

  - count_articles_per_product()              — no-input aggregate (§3)
  - top_products_by_revenue(p_n int)          — parameterized ordered TABLE (§4)

The country-named procedures referenced in tasks.md are stale relative to
the locked contracts/procedures.md webshop dataset; the picks here come
from the shipped sql/10_procedures.sql.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows (bridge + pywin32 paths)",
)

psycopg = pytest.importorskip("psycopg")

from examples.windows_sample_db.launcher import bridge_process  # noqa: E402
from examples.windows_sample_db.loader import (  # noqa: E402
    capture_state,
    load_dump_with_copy,
)
from examples.windows_sample_db.procedures import install  # noqa: E402
from examples.windows_sample_db.transport import TransportConfig  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def loaded_bridge(tmp_path: Path):
    """Boot a bridge against a fresh pgdata with the overlay + procedures installed.

    Uses the default tcp port offset by +11 to avoid collisions with other
    test files running in the same session.
    """
    data_dir = tmp_path / "pgdata"
    data_dir.mkdir()
    cfg = TransportConfig(kind="tcp", port=54331, data_dir=data_dir)

    state = capture_state(data_dir)
    with bridge_process(cfg) as handle:
        # Bootstrap: load the vendored dump (webshop.* tables + data),
        # then install overlay + role + procedures. Install is idempotent.
        with psycopg.connect(cfg.to_dsn(), connect_timeout=10, autocommit=False) as conn:
            load_dump_with_copy(conn, state.dump_path)
            install(conn, data_dir)
        yield cfg, handle


def test_count_articles_per_product_returns_nonempty_rows(loaded_bridge) -> None:
    """procedures.md §3: one row per product that has ≥1 article, ordered."""
    cfg, _ = loaded_bridge
    with psycopg.connect(cfg.to_dsn(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM count_articles_per_product()")
            rows = cur.fetchall()
    assert rows, "expected at least one (product_id, article_count) row"
    # Contract: article_count >= 1 for every returned row.
    assert all(r[1] >= 1 for r in rows)
    # Ordered by product_id ASC.
    product_ids = [r[0] for r in rows]
    assert product_ids == sorted(product_ids)


def test_top_products_by_revenue_returns_exactly_n_rows_desc(loaded_bridge) -> None:
    """procedures.md §4: top N ordered by revenue DESC, tiebreak product_id ASC."""
    cfg, _ = loaded_bridge
    with psycopg.connect(cfg.to_dsn(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM top_products_by_revenue(%s)", (5,))
            rows = cur.fetchall()
    assert len(rows) == 5
    revenues = [r[2] for r in rows]
    assert revenues == sorted(revenues, reverse=True), (
        f"revenue not descending: {revenues}"
    )


def test_top_products_by_revenue_rejects_non_positive_n(loaded_bridge) -> None:
    """procedures.md §4 error branch: p_n <= 0 → SQLSTATE 22023."""
    cfg, _ = loaded_bridge
    # autocommit avoids leaving an aborted transaction open after the expected
    # server error, which upsets psycopg's ``with conn`` cleanup path.
    conn = psycopg.connect(cfg.to_dsn(), connect_timeout=5, autocommit=True)
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.errors.Error) as ei:
                cur.execute("SELECT * FROM top_products_by_revenue(%s)", (0,))
        sqlstate = getattr(ei.value, "sqlstate", None) or ei.value.diag.sqlstate
        assert sqlstate == "22023"
    finally:
        conn.close()
