"""T064 — End-to-end quickstart runbook executor (SC-007).

Walks the 10 steps of ``specs/003-pglite-path-backup-restore/quickstart.md``
end-to-end against tmp paths, asserting each stage's documented outcome.

The runbook requires:
  - A working PGlite runtime (Node 20/22 + `@electric-sql/pglite`).
  - `pg_dump` / `psql` on PATH (or via the env overrides).

On hosts missing either, individual steps skip with a clear reason; on
CI with the T063a matrix gate both prerequisites are present and the
full sequence runs. Manual validation of SC-007 (10-minute unfamiliar-
operator run) is recorded separately under T068.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile

from pathlib import Path

import pytest

from pglite_pydb import PGliteConfig, PGliteManager
from pglite_pydb.cli.main import main as cli_main


pytestmark = pytest.mark.requires_pg_dump


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pglite-demo"
    d.mkdir()
    return d


@pytest.fixture
def backup_location(tmp_path: Path) -> Path:
    b = tmp_path / "pglite-demo-backups"
    b.mkdir()
    return b


# ---------------------------------------------------------------------------
# Step 1 — start an instance at an explicit path (US1)
# ---------------------------------------------------------------------------


def _populate_instance(data_dir: Path, pglite_runtime_available: bool) -> None:
    """Quickstart §1: create schema app, table widget, one row."""
    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    with PGliteManager(cfg) as m:
        conn = m.connect()
        try:
            cur = conn.cursor()
            cur.execute("CREATE SCHEMA IF NOT EXISTS app;")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS app.widget "
                "(id int PRIMARY KEY, name text);"
            )
            cur.execute(
                "INSERT INTO app.widget VALUES (1,'alpha') "
                "ON CONFLICT DO NOTHING;"
            )
            conn.commit()
        finally:
            conn.close()


def test_step1_start_instance_at_explicit_path(
    data_dir: Path, pglite_runtime_available: bool
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    assert (data_dir / "PG_VERSION").exists()


def test_step1b_restart_preserves_data(
    data_dir: Path, pglite_runtime_available: bool
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    with PGliteManager(cfg) as m:
        conn = m.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM app.widget WHERE id = 1;")
            assert cur.fetchone() == ("alpha",)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Step 2 — configure a backup location (FR-008..FR-011)
# ---------------------------------------------------------------------------


def test_step2_configure_backup_location(
    data_dir: Path,
    backup_location: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    assert rc == 0

    capsys.readouterr()  # drain
    rc = cli_main([
        "config", "--data-dir", str(data_dir), "get-backup-location"
    ])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert Path(out) == backup_location.resolve()


# ---------------------------------------------------------------------------
# Step 3 — logical backup, three selection modes
# ---------------------------------------------------------------------------


_LOGICAL_FN = re.compile(r"^\d{8}-\d{6}\.\d{3}(_\d+)?\.tar\.gz$")


def test_step3_logical_backup_single_schema(
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    rc = cli_main([
        "backup", "--data-dir", str(data_dir), "--schema", "app"
    ])
    assert rc == 0
    containers = [p for p in backup_location.iterdir() if _LOGICAL_FN.match(p.name)]
    assert len(containers) == 1

    with tarfile.open(containers[0], "r:gz") as tar:
        top = containers[0].name[: -len(".tar.gz")]
        names = tar.getnames()
        assert f"{top}/manifest.json" in names
        assert f"{top}/app.sql" in names

        mf = tar.extractfile(f"{top}/manifest.json")
        assert mf is not None
        manifest = json.loads(mf.read().decode("utf-8"))
        assert manifest["kind"] == "logical"
        assert manifest["included_schemas"] == ["app"]
        assert manifest["schema_version"] == 1


def test_step3_logical_backup_all_mode(
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    rc = cli_main(["backup", "--data-dir", str(data_dir), "--all"])
    assert rc == 0
    containers = [p for p in backup_location.iterdir() if _LOGICAL_FN.match(p.name)]
    assert len(containers) == 1
    with tarfile.open(containers[0], "r:gz") as tar:
        top = containers[0].name[: -len(".tar.gz")]
        mf = tar.extractfile(f"{top}/manifest.json")
        assert mf is not None
        manifest = json.loads(mf.read().decode("utf-8"))
        assert manifest["included_schemas"] == ["*"]


# ---------------------------------------------------------------------------
# Step 4 — full snapshot
# ---------------------------------------------------------------------------


_FULL_FN = re.compile(r"^FULL_SNAPSHOT_\d{8}-\d{6}\.\d{3}(_\d+)?\.tar\.gz$")


def test_step4_full_snapshot(
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    rc = cli_main(["backup", "--data-dir", str(data_dir), "--full-snapshot"])
    assert rc == 0
    containers = [p for p in backup_location.iterdir() if _FULL_FN.match(p.name)]
    assert len(containers) == 1
    with tarfile.open(containers[0], "r:gz") as tar:
        names = tar.getnames()
        assert any(n.endswith("/manifest.json") for n in names)
        assert any(n.endswith("/data/PG_VERSION") for n in names)
        # No sidecar inside — FR-032.
        assert not any("/data/.pglite-pydb" in n for n in names)


# ---------------------------------------------------------------------------
# Step 5 — restore logical by name
# ---------------------------------------------------------------------------


def test_step5_restore_logical_by_name(
    tmp_path: Path,
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    cli_main(["backup", "--data-dir", str(data_dir), "--schema", "app"])
    containers = sorted(p for p in backup_location.iterdir() if _LOGICAL_FN.match(p.name))
    assert containers

    restored = tmp_path / "restored"
    restored.mkdir()
    rc = cli_main([
        "restore", "--data-dir", str(restored), str(containers[-1]),
        "--assume-yes",
    ])
    assert rc == 0

    cfg = PGliteConfig(data_dir=restored, timeout=60)
    with PGliteManager(cfg) as m:
        conn = m.connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT name FROM app.widget WHERE id=1;")
            assert cur.fetchone() == ("alpha",)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Step 6 — restore --latest with --assume-yes in non-TTY
# ---------------------------------------------------------------------------


def test_step6_restore_latest_non_tty(
    tmp_path: Path,
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    cli_main(["backup", "--data-dir", str(data_dir), "--all"])

    restored = tmp_path / "restored-latest"
    restored.mkdir()
    cli_main([
        "config", "--data-dir", str(restored),
        "set-backup-location", str(backup_location),
    ])
    rc = cli_main([
        "restore", "--data-dir", str(restored), "--latest", "--assume-yes"
    ])
    assert rc == 0


# ---------------------------------------------------------------------------
# Step 7 — restore --overwrite conflict path
# ---------------------------------------------------------------------------


def test_step7_overwrite_conflict(
    tmp_path: Path,
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    cli_main(["backup", "--data-dir", str(data_dir), "--schema", "app"])
    containers = sorted(p for p in backup_location.iterdir() if _LOGICAL_FN.match(p.name))

    restored = tmp_path / "restored-conflict"
    restored.mkdir()
    # First restore creates schema 'app'.
    cli_main([
        "restore", "--data-dir", str(restored), str(containers[-1]),
        "--assume-yes",
    ])
    # Second restore without --overwrite must exit 13 (RestoreConflictError).
    rc = cli_main([
        "restore", "--data-dir", str(restored), str(containers[-1]),
        "--assume-yes",
    ])
    assert rc == 13

    # With --overwrite it proceeds.
    rc = cli_main([
        "restore", "--data-dir", str(restored), str(containers[-1]),
        "--overwrite", "--assume-yes",
    ])
    assert rc == 0


# ---------------------------------------------------------------------------
# Step 8 — full-snapshot two-stage confirmation (non-TTY path)
# ---------------------------------------------------------------------------


def test_step8_full_snapshot_two_stage(
    tmp_path: Path,
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    cli_main(["backup", "--data-dir", str(data_dir), "--full-snapshot"])

    # Empty target: single confirmation via --assume-yes.
    fresh = tmp_path / "pglite-fresh"
    fresh.mkdir()
    cli_main([
        "config", "--data-dir", str(fresh),
        "set-backup-location", str(backup_location),
    ])
    rc = cli_main([
        "restore", "--data-dir", str(fresh),
        "--full-snapshot", "--latest", "--assume-yes",
    ])
    assert rc == 0
    assert (fresh / "PG_VERSION").exists()

    # Non-empty target: --assume-yes alone is insufficient.
    rc = cli_main([
        "restore", "--data-dir", str(fresh),
        "--full-snapshot", "--latest", "--assume-yes",
    ])
    assert rc == 14  # ConfirmationRequiredError on the second prompt

    # With both flags, it proceeds.
    rc = cli_main([
        "restore", "--data-dir", str(fresh),
        "--full-snapshot", "--latest",
        "--assume-yes", "--assume-yes-destroy",
    ])
    assert rc == 0


# ---------------------------------------------------------------------------
# Step 9 — sidecar preservation across full-snapshot restore
# ---------------------------------------------------------------------------


def test_step9_sidecar_preserved_across_full_snapshot(
    tmp_path: Path,
    data_dir: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    _populate_instance(data_dir, pglite_runtime_available)
    cli_main([
        "config", "--data-dir", str(data_dir),
        "set-backup-location", str(backup_location),
    ])
    cli_main(["backup", "--data-dir", str(data_dir), "--full-snapshot"])

    target = tmp_path / "restored-sidecar"
    target.mkdir()
    # Configure a distinct backup_location on the target.
    target_backup = tmp_path / "target-backups"
    target_backup.mkdir()
    cli_main([
        "config", "--data-dir", str(target),
        "set-backup-location", str(target_backup),
    ])
    # Perform the restore (empty PGlite content, single confirmation).
    cli_main([
        "restore", "--data-dir", str(target),
        "--full-snapshot", "--latest",
        "--assume-yes", "--assume-yes-destroy",
    ])

    # Target's sidecar is preserved (still points at target_backup).
    sidecar = target / ".pglite-pydb" / "config.json"
    assert sidecar.exists()
    cfg = json.loads(sidecar.read_text("utf-8"))
    assert Path(cfg["backup_location"]) == target_backup.resolve()


# ---------------------------------------------------------------------------
# Step 10 — cross-platform portability (covered by
# test_cross_platform_portability.py — cross-reference only here).
# ---------------------------------------------------------------------------


def test_step10_cross_platform_reference() -> None:
    """Step 10 of the quickstart is validated by
    ``tests/test_cross_platform_portability.py``. This marker keeps the
    10-step mapping visible in test collection."""
    portability = Path(__file__).parent / "test_cross_platform_portability.py"
    assert portability.exists()
