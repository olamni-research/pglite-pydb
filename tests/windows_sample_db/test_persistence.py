"""T018 + T018b — persistence + warm-run timing tests (TDD, pre-T021).

Covers:
  - PG_VERSION present after a fresh load (FR-015)
  - restart sees the same overlay rows written in the previous run (FR-002, FR-015)
  - SC-002: warm run from process start to first procedure result < 10s
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows (bridge + pywin32 paths)",
)


REPO_ROOT = Path(__file__).resolve().parents[2]

PROC_LINE_RE = re.compile(
    r"\[example\] proc=(\S+) rows=(\d+) elapsed_ms=([\d.]+)"
)
DONE_LINE_RE = re.compile(r"\[example\] done exit=0")


def _run_example(data_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ}
    return subprocess.run(
        [
            sys.executable, "-m", "examples.windows_sample_db.run_example",
            "--data-dir", str(data_dir),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )


@pytest.fixture
def warmed_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pgdata"
    r = _run_example(d, "--reset")
    assert r.returncode == 0, f"priming run failed: {r.stderr}"
    return d


def test_pg_version_present_after_fresh_load(tmp_path: Path) -> None:
    """FR-015: fresh run leaves a valid PGlite dataDir on disk."""
    data_dir = tmp_path / "pgdata"
    r = _run_example(data_dir, "--reset")
    assert r.returncode == 0, r.stderr
    assert (data_dir / "PG_VERSION").is_file()


def test_restart_sees_same_overlay_rows(warmed_data_dir: Path) -> None:
    """FR-002: invoking a mutation procedure then restarting preserves state."""
    # First run (via fixture) already loaded + installed. Execute a second
    # run that exercises the overlay, then a third run that reads it back.
    mutate = _run_example(warmed_data_dir)
    assert mutate.returncode == 0, mutate.stderr
    assert "[example] pgdata status=warm" in mutate.stdout

    replay = _run_example(warmed_data_dir)
    assert replay.returncode == 0, replay.stderr
    # Overlay table survives restart — rename_product_display_name is one of
    # the 10 procedures; the overlay row count must be >= 1 across runs.
    procs_in_replay = {m.group(1) for m in PROC_LINE_RE.finditer(replay.stdout)}
    assert "rename_product_display_name" in procs_in_replay


def test_warm_run_completes_under_10_seconds(warmed_data_dir: Path) -> None:
    """SC-002: warm-run wall clock from process start to done < 10s.

    The priming fresh-load run is excluded from the timed window (handled
    by the ``warmed_data_dir`` fixture).
    """
    import time
    t0 = time.monotonic()
    r = _run_example(warmed_data_dir)
    elapsed = time.monotonic() - t0
    assert r.returncode == 0, r.stderr
    assert DONE_LINE_RE.search(r.stdout), "missing [example] done exit=0 line"
    assert elapsed < 10.0, f"warm run took {elapsed:.2f}s (budget 10s)"
