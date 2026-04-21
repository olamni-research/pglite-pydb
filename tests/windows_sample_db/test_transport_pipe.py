r"""T025 + T026 — named-pipe transport tests (TDD, pre-T027/T028/T029).

Covers:
  - connect as example_user over \\.\pipe\pglite_example, SELECT 1 works
    (transport.md §Windows named pipe, FR-020)
  - bridge process binds NO TCP listener during a pipe-only run (FR-009
    pipe branch — external verifiability)
  - bridge emits '[bridge] start transport=pipe listen=\\.\pipe\<name>'
    (transport.md Observability)
  - bridge-layer role gate also fires over the pipe transport (FR-021)
  - stable-pipe-name collision exits 5 and suggests --unique-pipe (FR-026)
  - --unique-pipe yields distinct per-process names, concurrent bridges
    both succeed (FR-025)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows (named pipes + pywin32)",
)

psycopg = pytest.importorskip("psycopg")
psutil = pytest.importorskip("psutil")
pytest.importorskip("win32file")  # pywin32

from examples.windows_sample_db.launcher import (  # noqa: E402
    PortInUseError,
    bridge_process,
)
from examples.windows_sample_db.transport import TransportConfig  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pipe_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pgdata"
    d.mkdir()
    return d


@pytest.fixture
def pipe_config(pipe_data_dir: Path) -> TransportConfig:
    # Unique pipe name keeps tests independent of any leftover dev runs.
    return TransportConfig(
        kind="pipe",
        pipe_name="pglite_example",
        unique_pipe=True,
        data_dir=pipe_data_dir,
    )


def _wait_for_accept(handle, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handle.accept_events():
            return
        time.sleep(0.05)
    raise AssertionError("no [bridge] accept line observed within timeout")


# ------------------------------ T025 ----------------------------------------


def test_pipe_connect_as_example_user_select_1(pipe_config: TransportConfig) -> None:
    with bridge_process(pipe_config) as handle:
        with psycopg.connect(handle.dsn(), connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone() == (1,)
        _wait_for_accept(handle)
        accepted = [e for e in handle.accept_events() if e.result == "accept"]
        assert accepted, "expected at least one accepted connection"
        assert accepted[-1].transport == "pipe"
        assert accepted[-1].role == "example_user"


def test_bridge_binds_no_tcp_port_during_pipe_run(
    pipe_config: TransportConfig,
) -> None:
    """FR-009 pipe branch: externally verifiable that bridge holds no TCP socket."""
    with bridge_process(pipe_config) as handle:
        # Wait for listener readiness then inspect the bridge's own sockets.
        _wait_for_accept_or_ready(handle, pipe_config)
        proc = psutil.Process(handle.process.pid)
        tcp_listeners = [
            c for c in proc.net_connections(kind="tcp")
            if c.status == psutil.CONN_LISTEN
        ]
        assert tcp_listeners == [], (
            f"bridge process bound {len(tcp_listeners)} TCP listener(s) "
            f"on a --transport pipe run: {tcp_listeners}"
        )


def _wait_for_accept_or_ready(handle, config: TransportConfig, timeout: float = 5.0) -> None:
    """Sleep until the bridge is ready for connections.

    launch_bridge already blocks on readiness for TCP; for pipe it polls the
    named pipe via WaitNamedPipeW. Either way, bridge_process returning means
    the bridge is accepting; this helper just gives the start-log line a moment
    to flush through the reader thread so tests can assert on it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Poll any byte of bridge output — start line arrives fast.
        time.sleep(0.1)
        return


def test_bridge_start_line_names_pipe_transport(
    pipe_config: TransportConfig, capfd: pytest.CaptureFixture[str]
) -> None:
    with bridge_process(pipe_config):
        time.sleep(0.5)
    out = capfd.readouterr().out
    assert "[bridge] start transport=pipe" in out
    assert pipe_config.pipe_path in out or pipe_config.resolved_pipe_name in out


def test_pipe_rejects_non_example_user_role(pipe_config: TransportConfig) -> None:
    """FR-021: bridge role gate applies to the pipe transport too."""
    with bridge_process(pipe_config) as handle:
        # Open a bare connection as role=postgres; the bridge must reject
        # before psycopg completes the handshake.
        bad_dsn = handle.dsn(role_override="postgres")
        with pytest.raises(psycopg.errors.Error) as ei:
            psycopg.connect(bad_dsn, connect_timeout=5)
        msg = str(ei.value).lower()
        assert "not permitted" in msg or "example server" in msg
        _wait_for_accept(handle)
        rejects = [e for e in handle.accept_events() if e.result == "reject"]
        assert rejects, "expected a reject event for role=postgres over pipe"
        assert rejects[-1].transport == "pipe"
        assert rejects[-1].role == "postgres"


# ------------------------------ T026 ----------------------------------------


def test_stable_pipe_name_collision_exits_5(tmp_path: Path) -> None:
    """FR-026: second bridge with same stable pipe name exits 5."""
    data_a = tmp_path / "pgdata_a"
    data_a.mkdir()
    data_b = tmp_path / "pgdata_b"
    data_b.mkdir()

    stable_cfg_a = TransportConfig(
        kind="pipe",
        pipe_name="pglite_example_collision_test",
        unique_pipe=False,
        data_dir=data_a,
    )
    stable_cfg_b = TransportConfig(
        kind="pipe",
        pipe_name="pglite_example_collision_test",
        unique_pipe=False,
        data_dir=data_b,
    )

    with bridge_process(stable_cfg_a):
        # Second bridge must fail fast with PortInUseError (exit code 5).
        with pytest.raises(PortInUseError) as ei:
            with bridge_process(stable_cfg_b):
                pass
        msg = str(ei.value)
        assert "pglite_example_collision_test" in msg
        assert "--unique-pipe" in msg, (
            f"collision message must suggest --unique-pipe per FR-026; got: {msg!r}"
        )


def test_unique_pipe_yields_distinct_concurrent_bridges(tmp_path: Path) -> None:
    """FR-025: --unique-pipe lets concurrent runs coexist on distinct names."""
    data_a = tmp_path / "pgdata_a"
    data_a.mkdir()
    data_b = tmp_path / "pgdata_b"
    data_b.mkdir()

    cfg_a = TransportConfig(
        kind="pipe",
        pipe_name="pglite_example",
        unique_pipe=True,
        data_dir=data_a,
    )
    cfg_b = TransportConfig(
        kind="pipe",
        pipe_name="pglite_example",
        unique_pipe=True,
        data_dir=data_b,
    )

    # Resolved names must differ even when the stem is shared.
    assert cfg_a.resolved_pipe_name != cfg_b.resolved_pipe_name

    with bridge_process(cfg_a) as handle_a, bridge_process(cfg_b) as handle_b:
        # Both bridges are alive and listening on their respective paths.
        assert handle_a.process.poll() is None
        assert handle_b.process.poll() is None

        with psycopg.connect(handle_a.dsn(), connect_timeout=5) as conn_a:
            with conn_a.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone() == (1,)
        with psycopg.connect(handle_b.dsn(), connect_timeout=5) as conn_b:
            with conn_b.cursor() as cur:
                cur.execute("SELECT 2")
                assert cur.fetchone() == (2,)
