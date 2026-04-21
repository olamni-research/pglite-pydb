"""T042 — argparse grammar for ``pglite-pydb restore``.

Covers invalid-flag-combination rows from contracts/cli.md §restore.
All pure-argparse failures exit 2; missing-selector (no container, no
``--latest``) exits 10 per FR-020. None of this touches pg_dump / psql /
PGlite — the tests run on any host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pglite_pydb.cli.main import main


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


def _run(argv: list[str]) -> int:
    try:
        return main(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


def test_no_selector_and_no_latest_exits_10(
    tmp_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-020: restore with no container and no --latest → exit 10."""
    rc = _run(["restore", "--data-dir", str(tmp_data_dir)])
    assert rc == 10
    assert "pglite-pydb: error:" in capsys.readouterr().err


def test_container_plus_latest_exits_2(
    tmp_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(
        [
            "restore",
            "--data-dir",
            str(tmp_data_dir),
            "20260421-143002.517.tar.gz",
            "--latest",
        ]
    )
    assert rc == 2
    assert capsys.readouterr().err


def test_full_snapshot_plus_overwrite_exits_2(
    tmp_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(
        [
            "restore",
            "--data-dir",
            str(tmp_data_dir),
            "--full-snapshot",
            "--latest",
            "--overwrite",
        ]
    )
    assert rc == 2
    assert capsys.readouterr().err


def test_full_snapshot_without_container_or_latest_exits_2(
    tmp_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = _run(
        ["restore", "--data-dir", str(tmp_data_dir), "--full-snapshot"]
    )
    assert rc == 2
    assert capsys.readouterr().err
