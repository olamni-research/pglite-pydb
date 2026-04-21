"""US3 integration tests for full-snapshot restore (T052–T058) plus T062.

Full-snapshot restore uses only stdlib ``tarfile`` for extraction — no
``pg_dump``/``psql`` are invoked — so most of these tests fabricate
containers on the fly and run on any host. Tests that need a real
PGlite instance on the source side use the ``pglite_runtime_available``
fixture from ``tests/conftest.py`` to skip cleanly.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import textwrap
import time

from pathlib import Path

import pytest

from pglite_pydb import PGliteConfig
from pglite_pydb._datadir import SIDECAR_DIRNAME
from pglite_pydb.backup import BackupEngine
from pglite_pydb.config import SidecarConfig
from pglite_pydb.errors import (
    ConfirmationDeclinedError,
    ConfirmationRequiredError,
    ContainerKindMismatchError,
    InstanceInUseError,
    InvalidDataDirError,
    NoBackupsFoundError,
)


# ---------------------------------------------------------------------------
# Helpers — fabricate minimal tar containers that mimic real ones
# ---------------------------------------------------------------------------


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _make_full_snapshot_container(
    location: Path,
    ts: str,
    data_tree: dict[str, bytes],
    *,
    manifest_overrides: dict | None = None,
) -> Path:
    """Write a fabricated FULL_SNAPSHOT_<ts>.tar.gz into ``location``.

    ``data_tree`` maps relative paths under ``data/`` to file bytes.
    """
    filename = f"FULL_SNAPSHOT_{ts}.tar.gz"
    path = location / filename
    top = f"FULL_SNAPSHOT_{ts}"
    manifest = {
        "schema_version": 1,
        "kind": "full-snapshot",
        "created_at": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T"
        f"{ts[9:11]}:{ts[11:13]}:{ts[13:15]}.{ts[16:19]}Z",
        "source_data_dir": "/fake/source",
        "pglite_pydb_version": "2026.4.21.1",
        "postgres_server_version": "unknown",
        "container_filename": filename,
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for rel, data in data_tree.items():
            _add_bytes(tar, f"{top}/data/{rel}", data)
        _add_bytes(
            tar,
            f"{top}/manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
        )
    return path


def _make_logical_container(
    location: Path, ts: str, schemas: list[str] | None = None
) -> Path:
    filename = f"{ts}.tar.gz"
    path = location / filename
    top = ts
    manifest = {
        "schema_version": 1,
        "kind": "logical",
        "created_at": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}T"
        f"{ts[9:11]}:{ts[11:13]}:{ts[13:15]}.{ts[16:19]}Z",
        "source_data_dir": "/fake/source",
        "included_schemas": schemas or ["app"],
        "pglite_pydb_version": "2026.4.21.1",
        "postgres_server_version": "unknown",
        "container_filename": filename,
    }
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for s in schemas or ["app"]:
            _add_bytes(tar, f"{top}/{s}.sql", b"-- empty\n")
        _add_bytes(
            tar,
            f"{top}/manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
        )
    return path


@pytest.fixture
def backup_location(tmp_path: Path) -> Path:
    loc = tmp_path / "backups"
    loc.mkdir()
    return loc


# ---------------------------------------------------------------------------
# T052 — by name into empty target
# ---------------------------------------------------------------------------


def test_full_snapshot_by_name_into_empty_target(
    tmp_path: Path, backup_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = _make_full_snapshot_container(
        backup_location,
        "20260421-140000.000",
        {"PG_VERSION": b"16\n", "base/1": b"payload-bytes"},
    )

    target = tmp_path / "target"  # does not exist yet
    target_cfg = PGliteConfig(data_dir=target, timeout=30)

    # Non-TTY + assume_yes → proceeds.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    BackupEngine(target_cfg).restore_full_snapshot(
        container, assume_yes=True
    )

    assert (target / "PG_VERSION").read_bytes() == b"16\n"
    assert (target / "base/1").read_bytes() == b"payload-bytes"
    # Source sidecar must NOT have leaked into the target.
    assert not (target / SIDECAR_DIRNAME / "config.json").exists()


# ---------------------------------------------------------------------------
# T053 — --latest scoping between logical and full-snapshot
# ---------------------------------------------------------------------------


def test_full_snapshot_latest_scoping(
    tmp_path: Path, backup_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pglite_pydb.backup import (
        list_full_snapshot_containers,
        list_logical_containers,
    )

    # Mix: two logical, two full-snapshot, at different timestamps.
    _make_logical_container(backup_location, "20260421-140000.001")
    _make_logical_container(backup_location, "20260421-150000.001")
    _make_full_snapshot_container(
        backup_location, "20260421-140000.002", {"PG_VERSION": b"16\n"}
    )
    _make_full_snapshot_container(
        backup_location, "20260421-150000.002", {"PG_VERSION": b"16\n"}
    )

    full_list = list_full_snapshot_containers(backup_location)
    logical_list = list_logical_containers(backup_location)
    assert full_list[-1] == "FULL_SNAPSHOT_20260421-150000.002.tar.gz"
    assert logical_list[-1] == "20260421-150000.001.tar.gz"

    # restore --full-snapshot --latest picks the FULL_SNAPSHOT one.
    target = tmp_path / "target"
    target.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(target)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    container_used = BackupEngine(target).restore_full_snapshot(
        "--latest", assume_yes=True
    )
    assert container_used.name == full_list[-1]


# ---------------------------------------------------------------------------
# T054 — two-stage confirmation over non-empty target
# ---------------------------------------------------------------------------


def test_full_snapshot_two_stage_over_non_empty_target(
    tmp_path: Path, backup_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = _make_full_snapshot_container(
        backup_location,
        "20260421-160000.000",
        {"PG_VERSION": b"16\n"},
    )

    # Non-empty target (contains non-allow-listed file).
    target = tmp_path / "target"
    target.mkdir()
    (target / "unrelated.txt").write_bytes(b"existing")

    # Non-TTY + only --assume-yes → second prompt requires
    # --assume-yes-destroy → ConfirmationRequiredError.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ConfirmationRequiredError):
        BackupEngine(target).restore_full_snapshot(container, assume_yes=True)

    # Both flags → proceeds.
    BackupEngine(target).restore_full_snapshot(
        container, assume_yes=True, assume_yes_destroy=True
    )
    assert (target / "PG_VERSION").read_bytes() == b"16\n"


# ---------------------------------------------------------------------------
# T055 — "completely empty" allow-list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seeded",
    [
        [],
        [SIDECAR_DIRNAME],
        [".DS_Store"],
        ["Thumbs.db"],
        ["desktop.ini"],
        [SIDECAR_DIRNAME, ".DS_Store", "Thumbs.db", "desktop.ini"],
    ],
)
def test_completely_empty_allow_list(
    tmp_path: Path,
    backup_location: Path,
    monkeypatch: pytest.MonkeyPatch,
    seeded: list[str],
) -> None:
    """Allow-listed seeds → single confirmation suffices."""
    container = _make_full_snapshot_container(
        backup_location,
        "20260421-170000.000",
        {"PG_VERSION": b"16\n"},
    )

    target = tmp_path / "target"
    target.mkdir()
    for name in seeded:
        entry = target / name
        if name == SIDECAR_DIRNAME:
            entry.mkdir()
        else:
            entry.write_bytes(b"")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # Just --assume-yes is enough when target is "completely empty".
    BackupEngine(target).restore_full_snapshot(container, assume_yes=True)
    assert (target / "PG_VERSION").read_bytes() == b"16\n"


def test_non_allow_listed_entry_fires_second_confirm(
    tmp_path: Path, backup_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = _make_full_snapshot_container(
        backup_location,
        "20260421-170000.001",
        {"PG_VERSION": b"16\n"},
    )
    target = tmp_path / "target"
    target.mkdir()
    (target / "random").write_bytes(b"")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ConfirmationRequiredError):
        BackupEngine(target).restore_full_snapshot(container, assume_yes=True)


# ---------------------------------------------------------------------------
# T056 — sidecar preservation (target's own sidecar survives)
# ---------------------------------------------------------------------------


def test_sidecar_preserved_and_none_created(
    tmp_path: Path, backup_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = _make_full_snapshot_container(
        backup_location,
        "20260421-180000.000",
        {"PG_VERSION": b"16\n"},
    )

    # (a) Target has its own sidecar with a distinct backup_location.
    target_a = tmp_path / "ta"
    target_a.mkdir()
    distinct = tmp_path / "my_other_backups"
    SidecarConfig(backup_location=str(distinct)).save(target_a)
    pre = (target_a / SIDECAR_DIRNAME / "config.json").read_bytes()

    cfg_a = PGliteConfig(data_dir=target_a, timeout=30)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    BackupEngine(cfg_a).restore_full_snapshot(
        container, assume_yes=True, assume_yes_destroy=True
    )
    post = (target_a / SIDECAR_DIRNAME / "config.json").read_bytes()
    assert pre == post, "target's sidecar was mutated by restore"

    # (b) Target has no sidecar pre-restore: no sidecar afterward either.
    target_b = tmp_path / "tb"
    cfg_b = PGliteConfig(data_dir=target_b, timeout=30)
    BackupEngine(cfg_b).restore_full_snapshot(container, assume_yes=True)
    assert not (target_b / SIDECAR_DIRNAME / "config.json").exists()


# ---------------------------------------------------------------------------
# T057 — kind mismatch rejected both directions
# ---------------------------------------------------------------------------


def test_kind_mismatch_rejected_both_directions(
    tmp_path: Path, backup_location: Path
) -> None:
    logical = _make_logical_container(backup_location, "20260421-190000.000")
    full = _make_full_snapshot_container(
        backup_location, "20260421-190000.000", {"PG_VERSION": b"16\n"}
    )
    target = tmp_path / "target"
    target.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(target)

    # FULL_SNAPSHOT_* fed to restore_logical → ContainerKindMismatchError.
    # (restore_logical on kind-mismatch fails during manifest validation,
    # before any PGliteManager spawn.)
    cfg = PGliteConfig(data_dir=target, timeout=30)
    with pytest.raises(ContainerKindMismatchError):
        BackupEngine(cfg).restore_logical([full])

    # Plain <ts>.tar.gz fed to restore_full_snapshot → ContainerKindMismatchError.
    with pytest.raises(ContainerKindMismatchError):
        BackupEngine(target).restore_full_snapshot(logical, assume_yes=True)


# ---------------------------------------------------------------------------
# T058 — acquires lock
# ---------------------------------------------------------------------------


_SUBPROC_LOCK_HOLDER = """
import sys, time
from pathlib import Path
sys.path.insert(0, {src!r})
from pglite_pydb._lock import InstanceLock
lock = InstanceLock(Path({data_dir!r})).acquire()
Path({ready!r}).write_text("ready")
try:
    time.sleep(30)
finally:
    lock.release()
"""


def test_full_snapshot_acquires_lock(
    tmp_path: Path, backup_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = _make_full_snapshot_container(
        backup_location,
        "20260421-200000.000",
        {"PG_VERSION": b"16\n"},
    )

    target = tmp_path / "target"
    target.mkdir()
    (target / SIDECAR_DIRNAME).mkdir()  # predicate: still "completely empty"

    src = str(Path(__file__).resolve().parents[1] / "src")
    ready = tmp_path / "ready"
    script = _SUBPROC_LOCK_HOLDER.format(
        src=src, data_dir=str(target), ready=str(ready)
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists()

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        with pytest.raises(InstanceInUseError):
            BackupEngine(target).restore_full_snapshot(container, assume_yes=True)
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait()


# ---------------------------------------------------------------------------
# T062 — FAILED_RESTORE sentinel blocks future manager.start()
# ---------------------------------------------------------------------------


def test_failed_restore_sentinel_blocks_future_start(
    tmp_path: Path,
) -> None:
    """If ``<data-dir>/.pglite-pydb/FAILED_RESTORE`` exists, start() raises
    ``InvalidDataDirError`` before spawning Node (T062)."""
    from pglite_pydb import PGliteManager

    data_dir = tmp_path / "busted"
    data_dir.mkdir()
    sidecar = data_dir / SIDECAR_DIRNAME
    sidecar.mkdir()
    # Fake a completed PGlite layout so other predicates pass.
    (data_dir / "PG_VERSION").write_text("16\n")
    (sidecar / "FAILED_RESTORE").write_text("mid-extraction crash\n")

    cfg = PGliteConfig(data_dir=data_dir, timeout=30)
    mgr = PGliteManager(cfg)
    with pytest.raises(InvalidDataDirError) as excinfo:
        mgr.start()
    msg = str(excinfo.value)
    assert "FAILED_RESTORE" in msg
