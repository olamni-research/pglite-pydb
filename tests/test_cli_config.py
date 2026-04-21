"""T035 — CLI contract for ``pglite-pydb config``.

Covers ``set-backup-location`` / ``get-backup-location`` / ``show`` per
contracts/cli.md §config and FR-009. The ``config`` command must NOT
acquire the instance lock and must NOT require the backup_location
directory to exist.
"""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from pglite_pydb.cli.main import main
from pglite_pydb.config import SidecarConfig


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "instance"
    d.mkdir()
    return d


def test_set_backup_location_writes_sidecar(
    data_dir: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "does_not_yet_exist"  # dir need not exist
    rc = main(
        ["config", "--data-dir", str(data_dir),
         "set-backup-location", str(target)]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    # Stdout reports the resolved absolute path.
    assert Path(out) == target.resolve()
    # Sidecar is persisted to disk.
    sc = SidecarConfig.load(data_dir)
    assert sc.backup_location == str(target.resolve())


def test_get_backup_location_when_unconfigured(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["config", "--data-dir", str(data_dir), "get-backup-location"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "(not configured)"


def test_get_backup_location_after_set(
    data_dir: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "backups"
    rc = main(
        ["config", "--data-dir", str(data_dir),
         "set-backup-location", str(target)]
    )
    assert rc == 0
    capsys.readouterr()  # discard the set-output
    rc = main(["config", "--data-dir", str(data_dir), "get-backup-location"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == str(target.resolve())


def test_show_prints_json(
    data_dir: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "backups"
    main(
        ["config", "--data-dir", str(data_dir),
         "set-backup-location", str(target)]
    )
    capsys.readouterr()
    rc = main(["config", "--data-dir", str(data_dir), "show"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": 1,
        "backup_location": str(target.resolve()),
    }


def test_config_does_not_require_existing_backup_dir(
    data_dir: Path, tmp_path: Path
) -> None:
    """FR-009 — set-backup-location accepts a non-existent directory.

    The existence check is deferred to ``backup`` / ``restore`` time
    (FR-011). ``config set-backup-location`` MUST succeed against a path
    that has never existed, and MUST NOT create it.
    """
    target = tmp_path / "not_yet_created"
    assert not target.exists()
    rc = main(
        ["config", "--data-dir", str(data_dir),
         "set-backup-location", str(target)]
    )
    assert rc == 0
    assert not target.exists()  # still not created


def test_config_action_missing_exits_2(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["config", "--data-dir", str(data_dir)])
    assert rc == 2
    assert "config" in capsys.readouterr().err
