"""T037 — cleanup and no-orphan-pipe assertions (FR-016, SC-007).

Runs the example in a scoped ``TemporaryDirectory`` and verifies:
  - the pgdata tree under the temp dir is reclaimable after the run (i.e.
    no stray file handles are holding it open),
  - no ``\\.\\pipe\\pglite_example*`` pipes linger after the bridge exits.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows",
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_example(data_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable, "-m", "examples.windows_sample_db.run_example",
            "--data-dir", str(data_dir),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=180,
    )


def _list_pglite_example_pipes() -> list[str]:
    """Return every ``\\.\\pipe\\pglite_example*`` currently present on the box."""
    names: list[str] = []
    for name in os.listdir(r"\\.\pipe"):
        if name.startswith("pglite_example"):
            names.append(name)
    return names


def test_tempdir_pgdata_reclaimed_after_run() -> None:
    """FR-016: ephemeral --data-dir must be fully removable after the run."""
    tmp = tempfile.TemporaryDirectory(prefix="pglite_cleanup_")
    try:
        data_dir = Path(tmp.name) / "pgdata"
        r = _run_example(data_dir, "--reset")
        assert r.returncode == 0, r.stderr
        # Touch sanity-check: PG_VERSION should exist post-run.
        assert (data_dir / "PG_VERSION").is_file()
    finally:
        tmp.cleanup()

    # After cleanup the tree must be gone (if any handle lingered, cleanup
    # would have raised PermissionError / OSError on Windows).
    assert not Path(tmp.name).exists()


def test_no_orphan_pglite_example_pipes_after_run() -> None:
    """SC-007: no stray pipes survive a clean run."""
    # Snapshot pipes present before the run — pytest's own session fixtures
    # may legitimately own bridges, so any pre-existing names are baseline.
    before = set(_list_pglite_example_pipes())

    with tempfile.TemporaryDirectory(prefix="pglite_cleanup_pipe_") as tmp:
        data_dir = Path(tmp) / "pgdata"
        r = _run_example(
            data_dir,
            "--reset", "--transport", "pipe", "--unique-pipe",
        )
        assert r.returncode == 0, r.stderr

    # Give the OS a moment to reclaim the pipe handle.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        after = set(_list_pglite_example_pipes())
        if after <= before:
            return
        time.sleep(0.1)

    leaked = sorted(after - before)
    assert not leaked, (
        f"orphan pglite_example* pipes remain after cleanup: {leaked}"
    )
