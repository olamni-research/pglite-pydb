"""T017 — TCP transport tests (TDD, pre-T021).

Covers:
  - connect as example_user, SELECT 1 succeeds (transport.md §TCP, FR-020)
  - bridge rejects any other role at the startup-packet layer (FR-021)
  - bridge emits '[bridge] start ...' and '[bridge] accept ...' lines
    (transport.md Observability, FR-019)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows (bridge + pywin32 paths)",
)

psycopg = pytest.importorskip("psycopg")

from examples.windows_sample_db.launcher import bridge_process  # noqa: E402
from examples.windows_sample_db.transport import TransportConfig  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tcp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pgdata"
    d.mkdir()
    return d


@pytest.fixture
def tcp_config(tcp_data_dir: Path) -> TransportConfig:
    # Port offset above the default 54320 to avoid collisions with a dev run.
    return TransportConfig(kind="tcp", port=54330, data_dir=tcp_data_dir)


def _wait_for_accept(handle, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if handle.accept_events():
            return
        time.sleep(0.05)
    raise AssertionError("no [bridge] accept line observed within timeout")


def test_tcp_connect_as_example_user_select_1(tcp_config: TransportConfig) -> None:
    with bridge_process(tcp_config) as handle:
        with psycopg.connect(tcp_config.to_dsn(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone() == (1,)
        _wait_for_accept(handle)
        accepted = [e for e in handle.accept_events() if e.result == "accept"]
        assert accepted, "expected at least one accepted connection"
        assert accepted[-1].transport == "tcp"
        assert accepted[-1].role == "example_user"


def test_tcp_rejects_non_example_user_role(tcp_config: TransportConfig) -> None:
    """FR-021: bridge-level role gate fires before the Postgres handshake."""
    bad_dsn = f"host={tcp_config.host} port={tcp_config.port} user=postgres dbname=postgres"
    with bridge_process(tcp_config) as handle:
        with pytest.raises(psycopg.errors.Error) as ei:
            psycopg.connect(bad_dsn, connect_timeout=5)
        msg = str(ei.value).lower()
        assert "not permitted" in msg or "example server" in msg
        _wait_for_accept(handle)
        rejects = [e for e in handle.accept_events() if e.result == "reject"]
        assert rejects, "expected a reject event for role=postgres"
        assert rejects[-1].role == "postgres"


def test_bridge_start_line_names_tcp_transport(
    tcp_config: TransportConfig, capfd: pytest.CaptureFixture[str]
) -> None:
    """transport.md Observability §: '[bridge] start transport=tcp ...'."""
    with bridge_process(tcp_config):
        # Give the reader threads a moment to flush the startup line.
        time.sleep(0.5)
    out = capfd.readouterr().out
    assert "[bridge] start transport=tcp" in out
    assert f"listen={tcp_config.host}:{tcp_config.port}" in out
