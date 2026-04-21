"""US2 integration tests for logical backup (T024–T032).

Each test marked ``requires_pg_dump`` auto-skips via tests/conftest.py
when PostgreSQL client tools are not on PATH. They also depend on the
``pglite_runtime_available`` fixture (conftest.py) which skips when this
host's PGlite + Node combination cannot produce a PG_VERSION.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import textwrap
import time

from pathlib import Path

import pytest

from pglite_pydb import PGliteConfig, PGliteManager
from pglite_pydb._platform import IS_WINDOWS
from pglite_pydb.backup import (
    BackupEngine,
    SchemaSelection,
    list_logical_containers,
)
from pglite_pydb.config import SidecarConfig
from pglite_pydb.errors import (
    BackupLocationNotConfiguredError,
    BackupLocationUnavailableError,
    InstanceInUseError,
    SchemaNotFoundError,
)


pytestmark = pytest.mark.requires_pg_dump


_LOGICAL_RE = re.compile(r"^\d{8}-\d{6}\.\d{3}(_\d+)?\.tar\.gz$")


@pytest.fixture
def backup_location(tmp_path: Path) -> Path:
    loc = tmp_path / "backups"
    loc.mkdir()
    return loc


@pytest.fixture
def live_instance(
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
):
    """Yield a running PGlite instance with a populated ``app`` schema."""
    import psycopg

    data_dir = tmp_path / "data"
    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    # Persist the backup_location in the sidecar BEFORE spawning the manager.
    data_dir.mkdir()
    sc = SidecarConfig(backup_location=str(backup_location))
    sc.save(data_dir)

    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute('CREATE SCHEMA app')
                cur.execute('CREATE SCHEMA analytics')
                cur.execute('CREATE TABLE app.users (id int primary key, name text)')
                cur.execute('CREATE TABLE analytics.events (ts int, v text)')
                cur.executemany(
                    'INSERT INTO app.users VALUES (%s,%s)',
                    [(i, f"u{i}") for i in range(5)],
                )
                cur.executemany(
                    'INSERT INTO analytics.events VALUES (%s,%s)',
                    [(i, f"e{i}") for i in range(3)],
                )
            conn.commit()
        # Stop so BackupEngine can acquire its own lock by default.
        mgr.stop()
        yield cfg
    finally:
        try:
            mgr.stop()
        except Exception:  # noqa: BLE001
            pass


def _open_manifest(container: Path) -> dict:
    with tarfile.open(container, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("/manifest.json"):
                f = tar.extractfile(member)
                assert f is not None
                return json.loads(f.read().decode("utf-8"))
    raise AssertionError(f"no manifest.json in {container}")


def _list_tar_entries(container: Path) -> list[str]:
    with tarfile.open(container, "r:gz") as tar:
        return tar.getnames()


# ---------------------------------------------------------------------------
# T024 — single schema
# ---------------------------------------------------------------------------


def test_single_schema(live_instance, backup_location: Path) -> None:
    engine = BackupEngine(live_instance)
    container = engine.create_logical(SchemaSelection.single("app"))
    assert container.exists()
    assert _LOGICAL_RE.match(container.name)
    entries = _list_tar_entries(container)
    assert any(n.endswith("/app.sql") for n in entries)
    assert any(n.endswith("/manifest.json") for n in entries)
    manifest = _open_manifest(container)
    assert manifest["kind"] == "logical"
    assert manifest["included_schemas"] == ["app"]
    assert manifest["schema_version"] == 1
    assert manifest["container_filename"] == container.name
    assert manifest["source_data_dir"] == str(live_instance.data_dir)


# ---------------------------------------------------------------------------
# T025 — list of schemas
# ---------------------------------------------------------------------------


def test_list_of_schemas(live_instance, backup_location: Path) -> None:
    engine = BackupEngine(live_instance)
    container = engine.create_logical(
        SchemaSelection.many(["app", "analytics"])
    )
    entries = _list_tar_entries(container)
    assert any(n.endswith("/app.sql") for n in entries)
    assert any(n.endswith("/analytics.sql") for n in entries)
    manifest = _open_manifest(container)
    assert manifest["included_schemas"] == ["app", "analytics"]


# ---------------------------------------------------------------------------
# T026 — --all mode
# ---------------------------------------------------------------------------


def test_all_mode(live_instance, backup_location: Path) -> None:
    engine = BackupEngine(live_instance)
    container = engine.create_logical(SchemaSelection.all())
    manifest = _open_manifest(container)
    assert manifest["included_schemas"] == ["*"]
    entries = _list_tar_entries(container)
    sql_files = [n for n in entries if n.endswith(".sql")]
    # At least one .sql per user schema we created; no system schemas.
    names = {os.path.basename(n) for n in sql_files}
    assert "app.sql" in names
    assert "analytics.sql" in names
    assert "pg_catalog.sql" not in names
    assert "information_schema.sql" not in names


# ---------------------------------------------------------------------------
# T027 — --all on a schemaless instance produces a valid empty container
# ---------------------------------------------------------------------------


def test_all_on_schemaless_instance_produces_valid_empty_container(
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    import psycopg

    data_dir = tmp_path / "empty_data"
    data_dir.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(data_dir)

    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        # Drop `public` if a default instance ships with it; we want zero
        # user schemas to exercise the edge case.
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            conn.commit()
    finally:
        mgr.stop()

    engine = BackupEngine(cfg)
    container = engine.create_logical(SchemaSelection.all())
    manifest = _open_manifest(container)
    assert manifest["included_schemas"] == ["*"]
    entries = _list_tar_entries(container)
    sql_entries = [n for n in entries if n.endswith(".sql")]
    assert sql_entries == []


# ---------------------------------------------------------------------------
# T028 — missing schema fails with no partial artefact
# ---------------------------------------------------------------------------


def test_missing_schema_fails_with_no_partial(
    live_instance, backup_location: Path
) -> None:
    engine = BackupEngine(live_instance)
    with pytest.raises(SchemaNotFoundError) as excinfo:
        engine.create_logical(SchemaSelection.single("does_not_exist"))
    assert "does_not_exist" in str(excinfo.value)
    # No container AND no .partial.
    leftovers = list(backup_location.iterdir())
    assert leftovers == [], f"stale artefacts left behind: {leftovers}"


# ---------------------------------------------------------------------------
# T029 — no backup location configured
# ---------------------------------------------------------------------------


def test_no_backup_location_configured_fails(
    tmp_path: Path, pglite_runtime_available: bool
) -> None:
    data_dir = tmp_path / "unconfigured"
    data_dir.mkdir()
    # Do NOT write a sidecar with backup_location — default is None.
    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    engine = BackupEngine(cfg)
    with pytest.raises(BackupLocationNotConfiguredError) as excinfo:
        engine.create_logical(SchemaSelection.all())
    assert "set-backup-location" in str(excinfo.value)


# ---------------------------------------------------------------------------
# T030 — unwritable location
# ---------------------------------------------------------------------------


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX chmod semantics")
def test_unwritable_location_fails_posix(
    live_instance, backup_location: Path
) -> None:
    backup_location.chmod(0o500)
    try:
        engine = BackupEngine(live_instance)
        with pytest.raises(BackupLocationUnavailableError):
            engine.create_logical(SchemaSelection.all())
    finally:
        backup_location.chmod(0o700)
    leftovers = list(backup_location.iterdir())
    assert all(not n.name.endswith(".partial") for n in leftovers)


# ---------------------------------------------------------------------------
# T031 — rapid-fire 10x unique
# ---------------------------------------------------------------------------


def test_rapid_fire_10x_unique(live_instance, backup_location: Path) -> None:
    """10 concurrent ``--force-hot`` logical backups produce 10 distinct names.

    NOTE: ``--force-hot`` attaches to an already-running server — we keep
    one PGliteManager alive for the duration of this test.
    """
    mgr = PGliteManager(live_instance)
    mgr.start()
    try:
        engine = BackupEngine(live_instance)

        def _one() -> Path:
            return engine.create_logical(
                SchemaSelection.all(), force_hot=True
            )

        start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: _one(), range(10)))
        elapsed = time.time() - start
    finally:
        mgr.stop()

    assert len({p.name for p in results}) == 10
    assert elapsed < 60, f"10x rapid-fire took {elapsed:.1f}s — > 60s budget"
    on_disk = list_logical_containers(backup_location)
    assert len(on_disk) == 10


# ---------------------------------------------------------------------------
# T032 — default mode requires exclusive lock; --force-hot attaches
# ---------------------------------------------------------------------------


def test_default_requires_exclusive_lock(
    live_instance, backup_location: Path
) -> None:
    mgr = PGliteManager(live_instance)
    mgr.start()
    try:
        engine = BackupEngine(live_instance)
        # Default path tries to acquire the lock — must fail.
        with pytest.raises(InstanceInUseError):
            engine.create_logical(SchemaSelection.all())
        # --force-hot succeeds against the same running server.
        container = engine.create_logical(
            SchemaSelection.all(), force_hot=True
        )
        assert container.exists()
    finally:
        mgr.stop()
