"""Shared pytest configuration for the pglite-pydb test suite.

Registers skip rules for the feature-003 markers so tests relying on
external tooling (`pg_dump`) are quietly skipped when the prerequisite is
absent, rather than erroring out.

Also exposes a module-scoped ``pglite_runtime_available`` fixture used by
any integration test that needs a real PGlite subprocess — on hosts where
PGlite 0.3 + Node 24 crashes before writing ``PG_VERSION`` the fixture
``pytest.skip()``s with a documented reason, rather than failing.
"""

from __future__ import annotations

import shutil

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    skip_no_pg_dump = pytest.mark.skip(
        reason="requires pg_dump on PATH (PostgreSQL 15+ client tools)",
    )
    pg_dump_available = shutil.which("pg_dump") is not None
    for item in items:
        if "requires_pg_dump" in item.keywords and not pg_dump_available:
            item.add_marker(skip_no_pg_dump)


@pytest.fixture(scope="module")
def pglite_runtime_available(tmp_path_factory: pytest.TempPathFactory) -> bool:
    """Probe once per module whether PGlite actually initialises on this host.

    Returns True on success, and calls ``pytest.skip`` on failure so that
    integration tests depending on a live server skip cleanly. Persistence
    of ``PG_VERSION`` is the marker — the TCP server can log "started"
    even when the async data-dir initialisation rejects.
    """
    from pglite_pydb import PGliteConfig
    from pglite_pydb import PGliteManager

    probe = tmp_path_factory.mktemp("pglite_probe") / "instance"
    cfg = PGliteConfig(data_dir=probe, timeout=30)
    mgr = PGliteManager(cfg)
    try:
        mgr.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PGlite runtime unavailable on this host: {exc!s}")
    finally:
        try:
            mgr.stop()
        except Exception:  # noqa: BLE001
            pass
    if not (probe / "PG_VERSION").exists():
        pytest.skip(
            "PGlite startup reported ready but PG_VERSION was not written "
            "(likely PGlite/Node compatibility issue on this host)."
        )
    return True
