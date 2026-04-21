"""T033 + T038 — session fixtures + upstream-immutability guard.

Scope
-----
- Windows-only collection gate (non-Windows → module skip).
- Session-scoped bridge pair sharing one ``dataDir`` per research R11:
  * ``bridge_tcp``  with ``--transport tcp`` on an ephemeral loopback port.
  * ``bridge_pipe`` with ``--transport pipe --unique-pipe`` on a
    per-session pipe name.
  A one-time bootstrap loads the vendored dump + installs the 10 procedures
  against the shared dataDir through the TCP bridge, then the pipe bridge
  is started against the same on-disk state.
- Parametrized function-scoped ``transport_conn`` fixture that yields a
  fresh psycopg 3 connection over whichever transport is under test.
- Session-autouse ``_upstream_immutability`` finalizer: MD5s key webshop
  tables before the tests start, re-reads them after the last test, and
  fails the session if anything changed (FR-023).
"""

from __future__ import annotations

import hashlib
import os
import socket
import sys

from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


# --- Windows-only gate -----------------------------------------------------


collect_ignore_glob: list[str] = []

if sys.platform != "win32":
    # Skip every test module under this directory on non-Windows.
    collect_ignore_glob = ["*.py"]

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows (feature 001-example-db-psycopg3-windows)",
)


# --- Imports guarded so non-Windows collection doesn't explode --------------


if sys.platform == "win32":
    import psycopg

    from examples.windows_sample_db.launcher import BridgeHandle
    from examples.windows_sample_db.launcher import bridge_process
    from examples.windows_sample_db.loader import capture_state
    from examples.windows_sample_db.loader import load_dump_with_copy
    from examples.windows_sample_db.procedures import install as install_procedures
    from examples.windows_sample_db.transport import TransportConfig


DUMP_LOADED_MARKER = ".dump_loaded"


def _dump_already_loaded(conn) -> bool:
    """True if ``webshop.products`` is present — cross-connection reliable.

    Using a SQL catalog lookup here rather than the on-disk
    ``INSTALL_MARKER`` because PGlite touches the dataDir at bridge
    startup (creates ``PG_VERSION``), so any file-presence heuristic
    has to be independent of that — a catalog probe is definitive.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'webshop' AND table_name = 'products'"
        )
        return cur.fetchone() is not None


def _free_tcp_port() -> int:
    """Bind :0 momentarily to get a guaranteed-free port for this session."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- Session-scoped bridges -------------------------------------------------


@pytest.fixture(scope="session")
def shared_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One dataDir for the whole session — both bridges share it (R11)."""
    d = tmp_path_factory.mktemp("pgdata_shared")
    return d.resolve()


@pytest.fixture(scope="session")
def bridge_tcp(shared_data_dir: Path) -> Iterator[BridgeHandle]:
    """TCP bridge plus one-time dump load + procedures install (bootstrap)."""
    cfg = TransportConfig(
        kind="tcp",
        host="127.0.0.1",
        port=_free_tcp_port(),
        data_dir=shared_data_dir,
    )
    with bridge_process(cfg) as handle:
        # Bootstrap the shared dataDir via this bridge. The pipe bridge
        # (started below) then inherits the fully-loaded state.
        # autocommit=True so the initial catalog probe doesn't leave an
        # open transaction that blocks ``load_dump_with_copy`` from
        # toggling autocommit.
        conn = psycopg.connect(handle.dsn(), connect_timeout=15, autocommit=True)
        try:
            if not _dump_already_loaded(conn):
                state = capture_state(shared_data_dir)
                load_dump_with_copy(conn, state.dump_path)
            install_procedures(conn, shared_data_dir)
        finally:
            conn.close()
        yield handle


@pytest.fixture(scope="session")
def bridge_pipe(
    shared_data_dir: Path,
    bridge_tcp: BridgeHandle,  # ordering dependency: TCP bootstraps first
) -> Iterator[BridgeHandle]:
    cfg = TransportConfig(
        kind="pipe",
        pipe_name="pglite_example",
        unique_pipe=True,
        data_dir=shared_data_dir,
    )
    with bridge_process(cfg) as handle:
        yield handle


@pytest.fixture(params=["tcp", "pipe"])
def transport_conn(
    request: pytest.FixtureRequest,
    bridge_tcp: BridgeHandle,
    bridge_pipe: BridgeHandle,
) -> Iterator[psycopg.Connection]:
    """Yield a psycopg 3 connection over the transport named by the param.

    Autocommit is on so that a procedure's ``RAISE EXCEPTION`` doesn't leave
    the transaction aborted and upset the ``with conn`` cleanup path.
    """
    handle = bridge_tcp if request.param == "tcp" else bridge_pipe
    conn = psycopg.connect(handle.dsn(), connect_timeout=10, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


# --- T038: Upstream immutability (FR-023) -----------------------------------


_UPSTREAM_TABLES = (
    "webshop.products",
    "webshop.articles",
    "webshop.customer",
    'webshop."order"',
    "webshop.order_positions",
)


def _md5_of_table(conn: psycopg.Connection, qname: str) -> str:
    """Deterministic MD5 over the row stream of ``qname``.

    Uses ``md5_agg``-style hashing in application code to avoid depending on
    any Postgres extension. Rows are ordered by primary key / first column so
    the checksum is stable across queries.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {qname} ORDER BY 1")
        h = hashlib.md5()  # non-crypto use: change-detection only
        for row in cur:
            h.update(repr(row).encode("utf-8"))
    return h.hexdigest()


@pytest.fixture(scope="session", autouse=True)
def _upstream_immutability(
    request: pytest.FixtureRequest,
    bridge_tcp: BridgeHandle,
) -> Iterator[None]:
    """Snapshot upstream tables at session start; verify unchanged at teardown."""
    # Skip entirely on non-Windows.
    if sys.platform != "win32":
        yield
        return

    with psycopg.connect(bridge_tcp.dsn(), connect_timeout=10) as conn:
        baseline = {t: _md5_of_table(conn, t) for t in _UPSTREAM_TABLES}

    yield

    # Re-check after every test in the session has run.
    with psycopg.connect(bridge_tcp.dsn(), connect_timeout=10) as conn:
        post = {t: _md5_of_table(conn, t) for t in _UPSTREAM_TABLES}

    diffs = [t for t in _UPSTREAM_TABLES if baseline[t] != post[t]]
    assert not diffs, (
        f"FR-023 violation — upstream tables mutated during the suite: {diffs}. "
        f"Only audit_log and product_overlay may be written by procedures."
    )
