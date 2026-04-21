"""US3 integration tests for logical restore (T043–T051).

Every test in this module depends on ``pg_dump`` and ``psql`` being on
PATH (PostgreSQL 15+ client tools). On hosts without them the module
auto-skips via the ``requires_pg_dump`` marker registered in
``tests/conftest.py``. Tests also use the ``pglite_runtime_available``
fixture from conftest to skip cleanly on hosts where PGlite cannot
produce a PG_VERSION file.
"""

from __future__ import annotations

import io
import json
import tarfile
import tempfile

from pathlib import Path

import pytest

from pglite_pydb import PGliteConfig, PGliteManager
from pglite_pydb.backup import BackupEngine, SchemaSelection
from pglite_pydb.config import SidecarConfig
from pglite_pydb.errors import (
    BackupLocationNotConfiguredError,
    ConfirmationDeclinedError,
    ConfirmationRequiredError,
    CorruptContainerError,
    MissingDataDirError,
    NoBackupsFoundError,
    RestoreConflictError,
)


pytestmark = pytest.mark.requires_pg_dump


@pytest.fixture
def backup_location(tmp_path: Path) -> Path:
    loc = tmp_path / "backups"
    loc.mkdir()
    return loc


@pytest.fixture
def produced_container(
    tmp_path: Path, backup_location: Path, pglite_runtime_available: bool
) -> tuple[Path, Path]:
    """Produce a real logical container with schema ``app`` (1 row).

    Returns (container_path, source_data_dir).
    """
    import psycopg

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(source_dir)
    cfg = PGliteConfig(data_dir=source_dir, timeout=60)
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA app")
                cur.execute("CREATE TABLE app.t (id int primary key, v text)")
                cur.execute("INSERT INTO app.t VALUES (1, 'one')")
            conn.commit()
    finally:
        mgr.stop()
    engine = BackupEngine(cfg)
    container = engine.create_logical(SchemaSelection.single("app"))
    return container, source_dir


@pytest.fixture
def fresh_target(tmp_path: Path, backup_location: Path) -> PGliteConfig:
    """A fresh, empty target data_dir with the backup_location pre-configured."""
    target = tmp_path / "target"
    target.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(target)
    return PGliteConfig(data_dir=target, timeout=60)


# ---------------------------------------------------------------------------
# T043 — by name, single container
# ---------------------------------------------------------------------------


def test_by_name_single_container(
    produced_container: tuple[Path, Path],
    fresh_target: PGliteConfig,
) -> None:
    import psycopg

    container, _ = produced_container
    engine = BackupEngine(fresh_target)
    engine.restore_logical([container])
    mgr = PGliteManager(fresh_target)
    mgr.start()
    try:
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, v FROM app.t ORDER BY id")
                rows = cur.fetchall()
        assert rows == [(1, "one")]
    finally:
        mgr.stop()


# ---------------------------------------------------------------------------
# T044 — list of containers
# ---------------------------------------------------------------------------


def test_by_name_list_of_containers(
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
    fresh_target: PGliteConfig,
) -> None:
    import psycopg

    # Produce two containers, each with a different schema.
    src = tmp_path / "src"
    src.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(src)
    cfg = PGliteConfig(data_dir=src, timeout=60)
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA a")
                cur.execute("CREATE TABLE a.t (id int)")
                cur.execute("INSERT INTO a.t VALUES (1)")
                cur.execute("CREATE SCHEMA b")
                cur.execute("CREATE TABLE b.t (id int)")
                cur.execute("INSERT INTO b.t VALUES (2)")
            conn.commit()
    finally:
        mgr.stop()
    eng = BackupEngine(cfg)
    c_a = eng.create_logical(SchemaSelection.single("a"))
    c_b = eng.create_logical(SchemaSelection.single("b"))

    BackupEngine(fresh_target).restore_logical([c_a, c_b])
    tm = PGliteManager(fresh_target)
    tm.start()
    try:
        with psycopg.connect(tm.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM a.t")
                assert cur.fetchall() == [(1,)]
                cur.execute("SELECT id FROM b.t")
                assert cur.fetchall() == [(2,)]
    finally:
        tm.stop()


# ---------------------------------------------------------------------------
# T045 — --latest with TTY confirmation
# ---------------------------------------------------------------------------


def test_latest_with_tty_confirmation(
    produced_container: tuple[Path, Path],
    fresh_target: PGliteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate TTY + user answering "y".
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _=None: "y")
    engine = BackupEngine(fresh_target)
    engine.restore_logical(["--latest"])

    # Decline path: TTY + user answers "n".
    monkeypatch.setattr("builtins.input", lambda _=None: "n")
    fresh2_dir = fresh_target.data_dir.parent / "target2"
    fresh2_dir.mkdir()
    SidecarConfig(
        backup_location=str(fresh_target.data_dir.parent / "backups")
    ).save(fresh2_dir)
    cfg2 = PGliteConfig(data_dir=fresh2_dir, timeout=60)
    with pytest.raises(ConfirmationDeclinedError):
        BackupEngine(cfg2).restore_logical(["--latest"])


# ---------------------------------------------------------------------------
# T046 — --latest non-TTY requires --assume-yes
# ---------------------------------------------------------------------------


def test_latest_non_tty_requires_assume_yes(
    produced_container: tuple[Path, Path],
    fresh_target: PGliteConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    engine = BackupEngine(fresh_target)
    with pytest.raises(ConfirmationRequiredError):
        engine.restore_logical(["--latest"])
    # With assume_yes → proceeds.
    engine.restore_logical(["--latest"], assume_yes=True)


# ---------------------------------------------------------------------------
# T047 — distinct errors for no-backups vs no-location
# ---------------------------------------------------------------------------


def test_no_backups_vs_no_location_distinct_errors(
    tmp_path: Path, pglite_runtime_available: bool
) -> None:
    # Case (a): configured but empty → NoBackupsFoundError.
    loc = tmp_path / "empty_loc"
    loc.mkdir()
    d1 = tmp_path / "d1"
    d1.mkdir()
    SidecarConfig(backup_location=str(loc)).save(d1)
    cfg1 = PGliteConfig(data_dir=d1, timeout=60)
    with pytest.raises(NoBackupsFoundError):
        BackupEngine(cfg1).restore_logical(["--latest"], assume_yes=True)

    # Case (b): no location configured → BackupLocationNotConfiguredError.
    d2 = tmp_path / "d2"
    d2.mkdir()
    cfg2 = PGliteConfig(data_dir=d2, timeout=60)
    with pytest.raises(BackupLocationNotConfiguredError):
        BackupEngine(cfg2).restore_logical(["--latest"], assume_yes=True)


# ---------------------------------------------------------------------------
# T048 — overwrite conflict + flow
# ---------------------------------------------------------------------------


def test_overwrite_conflict_and_flow(
    produced_container: tuple[Path, Path],
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg

    container, _ = produced_container

    # Prime a target instance that already has schema ``app``.
    target_dir = tmp_path / "primed"
    target_dir.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(target_dir)
    cfg = PGliteConfig(data_dir=target_dir, timeout=60)
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA app")
                cur.execute("CREATE TABLE app.existing (id int)")
            conn.commit()
    finally:
        mgr.stop()

    engine = BackupEngine(cfg)
    with pytest.raises(RestoreConflictError) as excinfo:
        engine.restore_logical([container])
    assert "app" in str(excinfo.value)

    # With --overwrite, non-TTY + no --assume-yes → ConfirmationRequiredError.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ConfirmationRequiredError):
        engine.restore_logical([container], overwrite=True)

    # With --overwrite + --assume-yes in non-TTY → proceeds.
    engine.restore_logical([container], overwrite=True, assume_yes=True)


# ---------------------------------------------------------------------------
# T049 — corrupt containers rejected
# ---------------------------------------------------------------------------


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_corrupt_container_rejected_truncated(
    tmp_path: Path, backup_location: Path, fresh_target: PGliteConfig
) -> None:
    bad = backup_location / "20260421-143002.517.tar.gz"
    bad.write_bytes(b"not a tar file, just garbage")
    with pytest.raises(CorruptContainerError):
        BackupEngine(fresh_target).restore_logical([bad])


def test_corrupt_container_rejected_bad_manifest(
    tmp_path: Path, backup_location: Path, fresh_target: PGliteConfig
) -> None:
    bad = backup_location / "20260421-143002.518.tar.gz"
    _write_tar(
        bad,
        {"20260421-143002.518/manifest.json": b"not json at all"},
    )
    with pytest.raises(CorruptContainerError):
        BackupEngine(fresh_target).restore_logical([bad])


def test_corrupt_container_rejected_future_schema_version(
    tmp_path: Path, backup_location: Path, fresh_target: PGliteConfig
) -> None:
    bad = backup_location / "20260421-143002.519.tar.gz"
    manifest = {
        "schema_version": 99,
        "kind": "logical",
        "included_schemas": ["app"],
    }
    _write_tar(
        bad,
        {
            "20260421-143002.519/manifest.json": json.dumps(manifest).encode(),
            "20260421-143002.519/app.sql": b"SELECT 1;",
        },
    )
    with pytest.raises(CorruptContainerError):
        BackupEngine(fresh_target).restore_logical([bad])


# ---------------------------------------------------------------------------
# T050 — atomicity on mid-restore failure
# ---------------------------------------------------------------------------


def test_atomicity_on_mid_restore_failure(
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    """A container whose .sql contains a failing DDL must leave the target
    unchanged (per-container BEGIN/COMMIT, FR-027)."""
    import psycopg

    # Produce a well-formed container with malformed SQL inside.
    bad = backup_location / "20260421-143002.600.tar.gz"
    manifest = {
        "schema_version": 1,
        "kind": "logical",
        "included_schemas": ["will_fail"],
    }
    bad_sql = b"CREATE SCHEMA will_fail;\nTHIS IS NOT VALID SQL;\n"
    _write_tar(
        bad,
        {
            "20260421-143002.600/manifest.json": json.dumps(manifest).encode(),
            "20260421-143002.600/will_fail.sql": bad_sql,
        },
    )

    target = tmp_path / "target"
    target.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(target)
    cfg = PGliteConfig(data_dir=target, timeout=60)
    # Prime with schema "keep".
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        with psycopg.connect(mgr.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA keep")
                cur.execute("CREATE TABLE keep.t (id int)")
                cur.execute("INSERT INTO keep.t VALUES (42)")
            conn.commit()
    finally:
        mgr.stop()

    with pytest.raises(Exception):  # noqa: BLE001
        BackupEngine(cfg).restore_logical([bad])

    mgr2 = PGliteManager(cfg)
    mgr2.start()
    try:
        with psycopg.connect(mgr2.get_dsn()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM keep.t")
                assert cur.fetchall() == [(42,)]
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name = 'will_fail'"
                )
                assert cur.fetchall() == []
    finally:
        mgr2.stop()


# ---------------------------------------------------------------------------
# T051 — missing --data-dir
# ---------------------------------------------------------------------------


def test_missing_data_dir_fails() -> None:
    """Driving the CLI without ``--data-dir`` exits 3 (MissingDataDirError)."""
    from pglite_pydb.cli.main import main

    try:
        rc = main(["restore", "--latest"])
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    assert rc == 3
