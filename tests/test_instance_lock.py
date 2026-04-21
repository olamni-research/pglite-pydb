"""Unit tests for the cross-platform advisory instance lock (T006 / FR-006).

Covers the acquire / release / contention / kill-release matrix described in
``specs/003-pglite-path-backup-restore/tasks.md`` T006 and
``specs/003-pglite-path-backup-restore/data-model.md`` §4.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

from pathlib import Path

import pytest

from pglite_pydb._lock import InstanceLock
from pglite_pydb._lock import lock_path_for
from pglite_pydb._platform import IS_WINDOWS
from pglite_pydb.errors import InstanceInUseError


def test_first_acquire_succeeds(tmp_path: Path) -> None:
    with InstanceLock(tmp_path) as lock:
        assert lock.path == lock_path_for(tmp_path.resolve())
        assert lock.path.exists()


def test_second_acquire_same_process_raises(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(InstanceInUseError) as excinfo:
            InstanceLock(tmp_path).acquire()
        assert str(tmp_path.resolve()) in str(excinfo.value)
    finally:
        first.release()


def test_release_on_context_exit_allows_subsequent_acquire(tmp_path: Path) -> None:
    with InstanceLock(tmp_path):
        pass
    # Should succeed now that the first lock was released.
    with InstanceLock(tmp_path):
        pass


def _subprocess_holder_script(data_dir: Path, ready_file: Path) -> str:
    """Return the script body a child process runs to hold the lock."""
    return textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})
        from pglite_pydb._lock import InstanceLock
        lock = InstanceLock(Path({str(data_dir)!r})).acquire()
        Path({str(ready_file)!r}).write_text("ready")
        try:
            time.sleep(30)
        finally:
            lock.release()
        """
    )


def test_second_acquire_from_subprocess_raises(tmp_path: Path) -> None:
    data_dir = tmp_path / "d"
    data_dir.mkdir()
    ready = tmp_path / "ready"
    script = _subprocess_holder_script(data_dir, ready)
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for the child to grab the lock.
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists(), "subprocess never reported lock acquisition"

        with pytest.raises(InstanceInUseError) as excinfo:
            InstanceLock(data_dir).acquire()
        assert str(data_dir.resolve()) in str(excinfo.value)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX SIGKILL semantics only")
def test_sigkill_releases_lock(tmp_path: Path) -> None:
    data_dir = tmp_path / "d"
    data_dir.mkdir()
    ready = tmp_path / "ready"
    script = _subprocess_holder_script(data_dir, ready)
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists()
        # Abrupt kill — kernel should release the flock on process death.
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    # Immediately acquiring again should succeed — kernel cleaned up.
    with InstanceLock(data_dir):
        pass


@pytest.mark.windows_only
def test_windows_msvcrt_path_acquires_and_releases(tmp_path: Path) -> None:
    """Windows-only: verify msvcrt.locking backend round-trips cleanly."""
    if not IS_WINDOWS:
        pytest.skip("Windows-only")
    with InstanceLock(tmp_path):
        pass
    # Re-acquire after release must succeed.
    with InstanceLock(tmp_path):
        pass
