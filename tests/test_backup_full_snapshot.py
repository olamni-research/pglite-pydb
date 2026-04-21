"""US2 integration tests for full-snapshot backup (T033, T034)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tarfile
import textwrap
import time

from pathlib import Path

import pytest

from pglite_pydb import PGliteConfig, PGliteManager
from pglite_pydb._datadir import SIDECAR_DIRNAME
from pglite_pydb.backup import BackupEngine, list_full_snapshot_containers
from pglite_pydb.cli.main import main as cli_main
from pglite_pydb.config import SidecarConfig
from pglite_pydb.errors import InstanceInUseError


_FULL_RE = re.compile(r"^FULL_SNAPSHOT_\d{8}-\d{6}\.\d{3}(_\d+)?\.tar\.gz$")


@pytest.fixture
def backup_location(tmp_path: Path) -> Path:
    loc = tmp_path / "backups"
    loc.mkdir()
    return loc


# ---------------------------------------------------------------------------
# T033 — layout + sidecar exclusion (needs a real PGlite PG_VERSION tree)
# ---------------------------------------------------------------------------


def test_full_snapshot_layout_and_sidecar_exclusion(
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(data_dir)

    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    # Populate data_dir with a real PGlite layout, then stop so the engine
    # can acquire the lock itself.
    mgr = PGliteManager(cfg)
    mgr.start()
    mgr.stop()

    assert (data_dir / "PG_VERSION").exists()
    # Sanity: the sidecar subtree is present under data_dir.
    assert (data_dir / SIDECAR_DIRNAME).is_dir()

    engine = BackupEngine(cfg)
    container = engine.create_full_snapshot()
    assert container.exists()
    assert _FULL_RE.match(container.name)

    with tarfile.open(container, "r:gz") as tar:
        names = tar.getnames()

    top_dir = container.name[: -len(".tar.gz")]
    assert any(n == f"{top_dir}/manifest.json" for n in names)
    assert any(n == f"{top_dir}/data/PG_VERSION" for n in names)
    # .pglite-pydb subtree must be absent (FR-032).
    assert not any(
        f"/data/{SIDECAR_DIRNAME}" in n or n.endswith(f"/{SIDECAR_DIRNAME}")
        for n in names
    ), f"sidecar leaked into full-snapshot archive: {names}"

    with tarfile.open(container, "r:gz") as tar:
        member = tar.getmember(f"{top_dir}/manifest.json")
        f = tar.extractfile(member)
        assert f is not None
        manifest = json.loads(f.read().decode("utf-8"))
    assert manifest["kind"] == "full-snapshot"
    assert "included_schemas" not in manifest
    assert manifest["container_filename"] == container.name
    assert manifest["source_data_dir"] == str(data_dir)


# ---------------------------------------------------------------------------
# T034 — full-snapshot requires lock; --force-hot is rejected at argparse
# ---------------------------------------------------------------------------


def test_full_snapshot_requires_lock_no_force_hot_at_argparse(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """argparse rejects ``--full-snapshot --force-hot`` with exit 2.

    This half of T034 does not need a live PGlite — the validation happens
    before any subprocess spawn.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    try:
        rc = cli_main(
            [
                "backup",
                "--data-dir",
                str(data_dir),
                "--full-snapshot",
                "--force-hot",
            ]
        )
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    assert rc == 2
    err = capsys.readouterr().err
    assert "--force-hot" in err or "full-snapshot" in err


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


def test_full_snapshot_requires_lock_live(
    tmp_path: Path,
    backup_location: Path,
    pglite_runtime_available: bool,
) -> None:
    """Full-snapshot MUST acquire the lock and fail when it's held.

    Uses a subprocess lock holder so we do not need a full PGliteManager
    just to demonstrate contention. The fixture still gates on PGlite
    being runnable because the data_dir needs PG_VERSION for a realistic
    snapshot.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    SidecarConfig(backup_location=str(backup_location)).save(data_dir)

    # Populate PG_VERSION via a real start (gated by the fixture).
    cfg = PGliteConfig(data_dir=data_dir, timeout=60)
    mgr = PGliteManager(cfg)
    mgr.start()
    mgr.stop()

    src = str(Path(__file__).resolve().parents[1] / "src")
    ready = tmp_path / "ready"
    script = _SUBPROC_LOCK_HOLDER.format(
        src=src, data_dir=str(data_dir), ready=str(ready)
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

        engine = BackupEngine(cfg)
        with pytest.raises(InstanceInUseError):
            engine.create_full_snapshot()
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait()
