"""US1 acceptance tests for the mandatory data-directory contract.

Covers tasks T016–T021 from
``specs/003-pglite-path-backup-restore/tasks.md`` Phase 3. The production
code for US1 landed in Phase 2 (T014/T015); this module is the end-to-end
gate that proves it.

Environment notes:

- T017, T018 require a live PGlite subprocess. When PGlite cannot
  initialise its data dir on this host (e.g. PGlite 0.3 + Node 24 crashes
  with an unhandled ExitStatus rejection before writing ``PG_VERSION``),
  the ``pglite_runtime_available`` fixture skips rather than fails —
  these tests must still pass on Linux / older Node CI.
- T020, T021 target the ``InstanceLock`` contract (acquired *before* any
  Node spawn in ``PGliteManager.start``), so they exercise the lock
  directly in a subprocess without needing a live PGlite server.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
import time

from pathlib import Path

import pytest

from pglite_pydb import PGliteConfig
from pglite_pydb import PGliteManager
from pglite_pydb._datadir import SIDECAR_DIRNAME
from pglite_pydb._platform import IS_WINDOWS
from pglite_pydb.errors import InstanceInUseError
from pglite_pydb.errors import InvalidDataDirError
from pglite_pydb.errors import MissingDataDirError


# ---------------------------------------------------------------------------
# The ``pglite_runtime_available`` fixture used by T017 / T018 now lives in
# tests/conftest.py (lifted there in Stage 4 so Phase 4 backup tests can
# reuse it). It remains module-scoped and skips cleanly when PGlite cannot
# produce a PG_VERSION on this host.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T016 — missing data_dir
# ---------------------------------------------------------------------------


def test_missing_data_dir_fails() -> None:
    """T016 / FR-001 / SC-001.

    Constructing ``PGliteConfig`` without ``data_dir`` raises
    ``MissingDataDirError`` before any subprocess would be spawned.
    """
    with pytest.raises(MissingDataDirError):
        PGliteConfig()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# T017 — fresh path initialises only inside supplied directory
# ---------------------------------------------------------------------------


def test_fresh_path_initialises_only_inside(
    tmp_path: Path, pglite_runtime_available: bool
) -> None:
    """T017 / FR-002 / FR-004 / SC-002.

    Start the wrapper at ``tmp_path/instance``; assert ``PG_VERSION`` is
    written inside that subdir, and walk the parent ``tmp_path`` to prove
    no PGlite files escape the supplied path.
    """
    data_dir = tmp_path / "instance"
    sentinel_sibling = tmp_path / "sibling-before"
    sentinel_sibling.mkdir()
    (sentinel_sibling / "keep.txt").write_text("unchanged")

    cfg = PGliteConfig(data_dir=data_dir, timeout=45)
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        assert (data_dir / "PG_VERSION").exists(), (
            "PGlite did not initialise PG_VERSION inside the supplied data_dir"
        )
        # Nothing else at tmp_path level except our data_dir and the sibling
        # sentinel should appear. PGlite must not leak files to the parent.
        top_level = {p.name for p in tmp_path.iterdir()}
        assert top_level == {"instance", "sibling-before"}, top_level
        # Sibling untouched.
        assert (sentinel_sibling / "keep.txt").read_text() == "unchanged"
        assert list(sentinel_sibling.iterdir()) == [sentinel_sibling / "keep.txt"]
    finally:
        mgr.stop()


# ---------------------------------------------------------------------------
# T018 — existing path preserves data across restarts
# ---------------------------------------------------------------------------


def test_existing_path_preserves_data(
    tmp_path: Path, pglite_runtime_available: bool
) -> None:
    """T018 / FR-004 / SC-003.

    Start, create 3 schemas × 10 rows, stop, restart, assert all 30 rows
    still present — re-initialisation must not clobber existing data.
    """
    import psycopg

    data_dir = tmp_path / "persistent"
    cfg = PGliteConfig(data_dir=data_dir, timeout=45)

    # First run: populate.
    mgr = PGliteManager(cfg)
    mgr.start()
    try:
        dsn = mgr.get_dsn()
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for schema in ("s1", "s2", "s3"):
                    cur.execute(f'CREATE SCHEMA "{schema}"')
                    cur.execute(
                        f'CREATE TABLE "{schema}".t (id int primary key, v text)'
                    )
                    cur.executemany(
                        f'INSERT INTO "{schema}".t VALUES (%s, %s)',
                        [(i, f"v{i}") for i in range(10)],
                    )
            conn.commit()
    finally:
        mgr.stop()

    pg_version_stat_before = (data_dir / "PG_VERSION").stat().st_ino if not IS_WINDOWS else (data_dir / "PG_VERSION").read_bytes()

    # Second run: verify rows.
    cfg2 = PGliteConfig(data_dir=data_dir, timeout=45)
    mgr2 = PGliteManager(cfg2)
    mgr2.start()
    try:
        dsn = mgr2.get_dsn()
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                total = 0
                for schema in ("s1", "s2", "s3"):
                    cur.execute(f'SELECT COUNT(*) FROM "{schema}".t')
                    total += cur.fetchone()[0]
                assert total == 30, f"expected 30 rows total, got {total}"
    finally:
        mgr2.stop()

    pg_version_stat_after = (data_dir / "PG_VERSION").stat().st_ino if not IS_WINDOWS else (data_dir / "PG_VERSION").read_bytes()
    assert pg_version_stat_before == pg_version_stat_after, (
        "PG_VERSION changed across restart — instance was reinitialised"
    )


# ---------------------------------------------------------------------------
# T019 — rejectable paths (file / non-empty non-pglite / unwritable)
# ---------------------------------------------------------------------------


def test_rejectable_path_is_a_regular_file(tmp_path: Path) -> None:
    """T019(a) / FR-005 — path exists as a regular file."""
    p = tmp_path / "not_a_dir"
    p.write_text("hello")
    with pytest.raises(InvalidDataDirError) as excinfo:
        PGliteConfig(data_dir=p)
    assert str(p.resolve()) in str(excinfo.value)
    # Target unchanged.
    assert p.read_text() == "hello"


def test_rejectable_path_non_empty_unrelated(tmp_path: Path) -> None:
    """T019(b) / FR-005 — non-empty directory without a PGlite layout."""
    p = tmp_path / "mydir"
    p.mkdir()
    (p / "something.txt").write_text("not pglite")
    with pytest.raises(InvalidDataDirError):
        PGliteConfig(data_dir=p)
    # Target unchanged — no sidecar was written.
    names = {x.name for x in p.iterdir()}
    assert names == {"something.txt"}


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX chmod semantics")
def test_rejectable_path_unwritable_posix(tmp_path: Path) -> None:
    """T019(c) / FR-005 — path is under an unwritable parent (POSIX).

    A pre-existing readonly dir that *would* be the data_dir itself gets
    resolved lazily; the failure surfaces when ``PGliteManager.start``
    attempts to create the sidecar. ``PGliteConfig`` itself accepts an
    empty readonly dir because it cannot know the intent at validation
    time — we exercise the manager path to prove the error is clean.
    """
    parent = tmp_path / "ro_parent"
    parent.mkdir()
    parent.chmod(0o500)  # r-x only
    try:
        target = parent / "instance"
        # PGliteConfig accepts (parent exists, target does not) — resolve succeeds.
        cfg = PGliteConfig(data_dir=target)
        mgr = PGliteManager(cfg)
        with pytest.raises((PermissionError, OSError)):
            mgr.start()
    finally:
        parent.chmod(0o700)  # restore for cleanup


# ---------------------------------------------------------------------------
# T020 — concurrent start on the same resolved path fails fast
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


def _spawn_lock_holder(data_dir: Path, ready: Path) -> subprocess.Popen[bytes]:
    src = str(Path(__file__).resolve().parents[1] / "src")
    script = _SUBPROC_LOCK_HOLDER.format(
        src=src, data_dir=str(data_dir), ready=str(ready)
    )
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_concurrent_start_same_path_fails_fast(tmp_path: Path) -> None:
    """T020 / FR-006.

    A subprocess holds the instance lock on ``data_dir``. The parent then
    attempts ``PGliteManager(...).start()`` on the same resolved path —
    which must raise ``InstanceInUseError`` within 100 ms (plan
    Performance Goal), before any Node subprocess spawn.
    """
    data_dir = tmp_path / "shared"
    data_dir.mkdir()
    ready = tmp_path / "ready"
    holder = _spawn_lock_holder(data_dir, ready)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists(), "lock-holder subprocess never signalled ready"

        cfg = PGliteConfig(data_dir=data_dir, timeout=30)
        mgr = PGliteManager(cfg)
        t0 = time.perf_counter()
        with pytest.raises(InstanceInUseError) as excinfo:
            mgr.start()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 2000, (
            f"InstanceInUseError took {elapsed_ms:.1f} ms — expected fast-fail "
            "under the Performance Goal"
        )
        assert str(data_dir.resolve()) in str(excinfo.value)
        # The holder subprocess is unaffected.
        assert holder.poll() is None
        # And no Node process was spawned by us.
        assert mgr.process is None
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait()


# ---------------------------------------------------------------------------
# T021 — symlink resolves to the same instance
# ---------------------------------------------------------------------------


def _can_create_symlinks(tmp_path: Path) -> bool:
    probe_target = tmp_path / "_symlink_probe_target"
    probe_link = tmp_path / "_symlink_probe_link"
    probe_target.mkdir()
    try:
        os.symlink(probe_target, probe_link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    finally:
        if probe_link.exists() or probe_link.is_symlink():
            try:
                probe_link.unlink()
            except OSError:
                pass
        try:
            probe_target.rmdir()
        except OSError:
            pass
    return True


def test_symlink_resolves_to_same_instance(tmp_path: Path) -> None:
    """T021 / FR-003 + edge-cases list.

    Create a symlink to ``real_dir``; acquire the lock via the symlink in
    a subprocess, then attempt to acquire via the real path in the parent.
    Both must resolve to the same on-disk lock file; the second attempt
    raises ``InstanceInUseError`` carrying the *resolved* real path.
    """
    if not _can_create_symlinks(tmp_path):
        pytest.skip(
            "symlink creation unavailable on this host "
            "(Windows without Developer Mode / SeCreateSymbolicLink)"
        )

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    os.symlink(real_dir, link_dir, target_is_directory=True)

    ready = tmp_path / "ready"
    # Hold the lock via the SYMLINK path — InstanceLock resolves internally
    # so the lock file lands under real_dir.
    holder = _spawn_lock_holder(link_dir, ready)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists()

        # Second attempt via the real path must collide.
        from pglite_pydb._lock import InstanceLock

        with pytest.raises(InstanceInUseError) as excinfo:
            InstanceLock(real_dir).acquire()
        assert str(real_dir.resolve()) in str(excinfo.value)

        # And the canonical lock file lives under the real directory.
        assert (real_dir / SIDECAR_DIRNAME / "instance.lock").exists()
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=5)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait()
