"""T023 — argparse grammar for ``pglite-pydb backup``.

Covers the invalid-flag-combination matrix from contracts/cli.md §backup.
Every row must exit with 2 (argparse-default usage error) before any
business logic runs.
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


@pytest.mark.parametrize(
    "args_tail",
    [
        # No selector at all.
        [],
        # --schema + --all mutually exclusive.
        ["--schema", "app", "--all"],
        # --schema + --full-snapshot mutually exclusive.
        ["--schema", "app", "--full-snapshot"],
        # --force-hot + --full-snapshot forbidden.
        ["--full-snapshot", "--force-hot"],
        # --force-hot requires a logical selector.
        ["--force-hot"],
    ],
)
def test_invalid_flag_combinations_exit_2(
    tmp_data_dir: Path,
    capsys: pytest.CaptureFixture[str],
    args_tail: list[str],
) -> None:
    argv = ["backup", "--data-dir", str(tmp_data_dir), *args_tail]
    # argparse-style exits from the mutex group raise SystemExit(2); our
    # manual validators return 2. Both count as the contract. Use try/except
    # to collapse both shapes.
    try:
        rc = main(argv)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0

    assert rc == 2, (
        f"expected exit 2 for invalid args {args_tail!r}, got {rc}"
    )
    err = capsys.readouterr().err
    assert err, "expected a usage/error message on stderr"
