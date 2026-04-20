"""T016 — loader tests (TDD, pre-T021).

Covers:
  - fresh run loads the vendored dump into an empty pgdata (FR-001)
  - SHA-256 mismatch aborts with exit code 3
  - warm run reuses pgdata (FR-002, FR-015)
  - data directory in a partial state raises exit code 6
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires Windows (bridge + pywin32 paths)",
)


# Lazy imports: keep this module importable on non-Windows for collection.
from examples.windows_sample_db.loader import (  # noqa: E402
    DataDirInconsistentError,
    DumpIntegrityError,
    PgDataStatus,
    capture_state,
    detect_pgdata_status,
    ensure_loadable,
    read_expected_checksum,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "examples" / "windows_sample_db" / "data"


# ----------------------------- unit-level -----------------------------------


def test_fresh_status_when_data_dir_missing(tmp_path: Path) -> None:
    assert detect_pgdata_status(tmp_path / "does_not_exist") is PgDataStatus.FRESH


def test_fresh_status_when_data_dir_empty(tmp_path: Path) -> None:
    assert detect_pgdata_status(tmp_path) is PgDataStatus.FRESH


def test_warm_status_when_pg_version_present(tmp_path: Path) -> None:
    (tmp_path / "PG_VERSION").write_text("17\n", encoding="ascii")
    assert detect_pgdata_status(tmp_path) is PgDataStatus.WARM


def test_inconsistent_status_when_leftover_without_pg_version(tmp_path: Path) -> None:
    (tmp_path / "postmaster.opts").write_text("stale\n", encoding="ascii")
    assert detect_pgdata_status(tmp_path) is PgDataStatus.INCONSISTENT


def test_capture_state_vendored_dump_matches_sidecar(tmp_path: Path) -> None:
    state = capture_state(tmp_path, data_root=DATA_ROOT)
    assert state.pgdata_status is PgDataStatus.FRESH
    assert len(state.expected_sha256) == 64
    assert state.dump_size > 0


def test_capture_state_raises_exit_code_3_on_sha_mismatch(tmp_path: Path) -> None:
    # Stage a copy of the dump with a deliberately broken sidecar.
    staged = tmp_path / "staged"
    staged.mkdir()
    shutil.copy(DATA_ROOT / "sample_db.sql", staged / "sample_db.sql")
    (staged / "sample_db.sql.sha256").write_text(
        "0" * 64 + "  sample_db.sql\n", encoding="ascii",
    )
    with pytest.raises(DumpIntegrityError) as ei:
        capture_state(tmp_path / "pgdata", data_root=staged)
    assert ei.value.exit_code == 3
    assert "sha-256 mismatch" in str(ei.value)


def test_read_expected_checksum_accepts_sha256sum_format(tmp_path: Path) -> None:
    digest = hashlib.sha256(b"x").hexdigest()
    f = tmp_path / "c.sha256"
    f.write_text(f"{digest}  foo.sql\n", encoding="ascii")
    assert read_expected_checksum(f) == digest


def test_ensure_loadable_raises_exit_code_6_on_inconsistent(tmp_path: Path) -> None:
    (tmp_path / "postmaster.opts").write_text("stale\n", encoding="ascii")
    state = capture_state(tmp_path, data_root=DATA_ROOT)
    assert state.pgdata_status is PgDataStatus.INCONSISTENT
    with pytest.raises(DataDirInconsistentError) as ei:
        ensure_loadable(state, allow_reset=False)
    assert ei.value.exit_code == 6
    assert "--reset" in str(ei.value)


def test_ensure_loadable_tolerates_inconsistent_under_allow_reset(
    tmp_path: Path,
) -> None:
    (tmp_path / "postmaster.opts").write_text("stale\n", encoding="ascii")
    state = capture_state(tmp_path, data_root=DATA_ROOT)
    ensure_loadable(state, allow_reset=True)  # must not raise


# --------------------------- integration (TDD-failing) ----------------------


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
        timeout=120,
    )


@pytest.mark.windows_sample_db
def test_fresh_run_installs_procedures_and_populates_pgdata(tmp_path: Path) -> None:
    """FR-001 + FR-015. Currently fails: run_example emits '<pending T021>'."""
    data_dir = tmp_path / "pgdata"
    r = _run_example(data_dir, "--reset")
    assert r.returncode == 0, r.stderr
    assert "[example] pgdata status=fresh" in r.stdout
    assert "[example] procedures installed=10 of 10" in r.stdout
    assert (data_dir / "PG_VERSION").is_file()


@pytest.mark.windows_sample_db
def test_warm_run_logs_warm_status_and_skips_install(tmp_path: Path) -> None:
    """FR-002. Currently fails: run_example doesn't install on the fresh run."""
    data_dir = tmp_path / "pgdata"
    first = _run_example(data_dir, "--reset")
    assert first.returncode == 0, first.stderr
    second = _run_example(data_dir)
    assert second.returncode == 0, second.stderr
    assert "[example] pgdata status=warm" in second.stdout
    assert "skipped=" in second.stdout  # install block records skip count on warm
