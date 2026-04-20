"""Windows sample-database example — CLI entry point (T015/T021/T022).

Argparse + non-Windows guard + ``--reset`` land in T015. T021 adds the
``invoke_all_procedures`` driver that calls all 10 webshop procedures over
the bridge, and T022 emits the full ordered ``[example]`` log block from
``contracts/cli.md``.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from examples.windows_sample_db import transport as _transport
from examples.windows_sample_db.launcher import (
    BridgeSpawnError,
    BridgeStartTimeout,
    LauncherError,
    PortInUseError,
    bridge_process,
)
from examples.windows_sample_db.loader import DumpIntegrityError
from examples.windows_sample_db.loader import LoaderError
from examples.windows_sample_db.loader import PgDataStatus
from examples.windows_sample_db.loader import capture_state
from examples.windows_sample_db.loader import ensure_loadable
from examples.windows_sample_db.loader import load_dump_with_copy
from examples.windows_sample_db.procedures import PROCEDURE_COUNT
from examples.windows_sample_db.procedures import install as install_procedures


LOG = logging.getLogger("example")

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m examples.windows_sample_db.run_example",
        description="Windows PGlite sample-database example (TCP + named pipe).",
    )
    parser.add_argument(
        "--transport",
        choices=("tcp", "pipe"),
        default=os.environ.get("PGLITE_EXAMPLE_TRANSPORT", "tcp"),
    )
    parser.add_argument("--host", type=str, default=_transport.DEFAULT_TCP_HOST)
    parser.add_argument("--port", type=int, default=_transport.DEFAULT_TCP_PORT)
    parser.add_argument(
        "--pipe-name", type=str, default=_transport.DEFAULT_PIPE_NAME
    )
    parser.add_argument("--unique-pipe", action="store_true", default=False)
    parser.add_argument(
        "--data-dir", type=Path, default=_transport.default_data_dir()
    )
    parser.add_argument("--reset", action="store_true", default=False)
    parser.add_argument("--log-level", choices=LOG_LEVELS, default="INFO")
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )


def _reset_data_dir(data_dir: Path) -> None:
    if data_dir.exists():
        shutil.rmtree(data_dir)


def _emit_transport_line(args: argparse.Namespace, data_dir_abs: Path) -> None:
    pipe = args.pipe_name if args.transport == "pipe" else "-"
    LOG.info(
        "[example] transport=%s host=%s port=%d pipe=%s data_dir=%s",
        args.transport, args.host, args.port, pipe, data_dir_abs,
    )


# ---------------------------------------------------------------------------
# Procedure driver (T021)
# ---------------------------------------------------------------------------


def _discover_inputs(conn: Any) -> dict[str, Any]:
    """Look up representative live keys from the loaded dump.

    Avoids hard-coding values that could drift if the upstream dump is
    re-vendored. Falls back to sensible defaults if a lookup returns empty.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email FROM webshop.customer ORDER BY id LIMIT 1"
        )
        row = cur.fetchone()
        customer_id, customer_email = (row[0], row[1]) if row else (1, "unknown@example.com")

        cur.execute("SELECT id FROM webshop.products ORDER BY id LIMIT 1")
        row = cur.fetchone()
        product_id = row[0] if row else 1

    return {
        "customer_id": int(customer_id),
        "customer_email": str(customer_email),
        "product_id": int(product_id),
    }


def _call(conn: Any, proc: str, sql: str, params: tuple[Any, ...]) -> int:
    """Execute a procedure call; return the row count or 1 for scalar/void."""
    t0 = time.monotonic()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        # TABLE / SETOF -> description present; scalar int or void -> same.
        try:
            rows = cur.fetchall()
            row_count = len(rows)
        except Exception:
            row_count = 0
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    LOG.info(
        "[example] proc=%s rows=%d elapsed_ms=%.2f",
        proc, row_count, elapsed_ms,
    )
    return row_count


def invoke_all_procedures(conn: Any) -> None:
    """Call each of the 10 webshop procedures once and log the result line.

    Inputs are discovered from the live dump where possible to avoid
    hard-coding identity values. The order mirrors ``contracts/procedures.md``.
    """
    k = _discover_inputs(conn)
    cid, email, pid = k["customer_id"], k["customer_email"], k["product_id"]

    _call(conn, "get_customer_by_email",
          "SELECT * FROM get_customer_by_email(%s)", (email,))
    _call(conn, "list_articles_for_product",
          "SELECT * FROM list_articles_for_product(%s, %s, %s)", (pid, 10, 1))
    _call(conn, "count_articles_per_product",
          "SELECT * FROM count_articles_per_product()", ())
    _call(conn, "top_products_by_revenue",
          "SELECT * FROM top_products_by_revenue(%s)", (5,))
    _call(conn, "list_orders_for_customer",
          "SELECT * FROM list_orders_for_customer(%s)", (cid,))
    _call(conn, "articles_in_category",
          "SELECT * FROM articles_in_category(%s)", ("Footwear",))
    _call(conn, "customer_order_report",
          "SELECT * FROM customer_order_report(%s)", (cid,))
    _call(conn, "bulk_log_articles_for_product",
          "SELECT bulk_log_articles_for_product(%s)", (pid,))
    _call(conn, "rename_product_display_name",
          "SELECT rename_product_display_name(%s, %s)",
          (pid, "Classic Runner"))
    _call(conn, "assert_product_exists",
          "SELECT assert_product_exists(%s)", (pid,))
    conn.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.log_level)

    if sys.platform != "win32":
        sys.stderr.write(
            "requires Windows: this example uses Windows-specific transports "
            "(TCP loopback + named pipes); re-run on a Windows host.\n"
        )
        return 2

    if args.transport != "tcp":
        sys.stderr.write(
            f"transport {args.transport!r} unavailable in this slice: "
            "only --transport tcp is wired up in US1; pipe lands with US2. "
            "Rerun with --transport tcp.\n"
        )
        return 4

    data_dir_abs = Path(args.data_dir).resolve()
    if args.reset:
        _reset_data_dir(data_dir_abs)

    _emit_transport_line(args, data_dir_abs)

    try:
        state = capture_state(data_dir_abs)
        ensure_loadable(state, allow_reset=args.reset)
    except DumpIntegrityError as e:
        sys.stderr.write(f"{e}\n")
        return e.exit_code
    except LoaderError as e:
        sys.stderr.write(f"{e}\n")
        return e.exit_code

    LOG.info(
        "[example] dump=ok sha256=%s... (%d bytes)",
        state.expected_sha256[:12], state.dump_size,
    )
    pgdata_label = "fresh" if state.pgdata_status is PgDataStatus.FRESH else "warm"
    LOG.info("[example] pgdata status=%s", pgdata_label)

    import psycopg  # local import — non-Windows platforms never reach here

    cfg = _transport.TransportConfig(
        kind="tcp",
        host=args.host,
        port=args.port,
        data_dir=data_dir_abs,
    )

    try:
        bridge_ctx = bridge_process(cfg)
    except LauncherError as e:
        sys.stderr.write(f"{e}\n")
        return e.exit_code

    try:
        with bridge_ctx as _handle, contextlib.closing(
            psycopg.connect(cfg.to_dsn(), connect_timeout=10)
        ) as conn:
            # Fresh load: run the vendored dump inside PGlite before installing
            # the overlay + procedures. Warm runs skip this entirely.
            if state.pgdata_status is PgDataStatus.FRESH:
                load_dump_with_copy(conn, state.dump_path)

            result = install_procedures(conn, data_dir_abs)
            skipped = 0 if result.ran else PROCEDURE_COUNT
            LOG.info(
                "[example] procedures installed=%d of %d (skipped=%d)",
                PROCEDURE_COUNT, PROCEDURE_COUNT, skipped,
            )
            LOG.info(
                "[example] connected as role=%s transport=%s",
                _transport.DEFAULT_ROLE, args.transport,
            )

            invoke_all_procedures(conn)
    except PortInUseError as e:
        sys.stderr.write(f"{e}\n")
        return e.exit_code
    except BridgeStartTimeout as e:
        sys.stderr.write(f"{e}\n")
        return e.exit_code
    except BridgeSpawnError as e:
        sys.stderr.write(f"{e}\n")
        return e.exit_code
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"unexpected error: {e}\n")
        return 1

    LOG.info("[example] done exit=0")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
