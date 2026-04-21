"""Unit tests for the sidecar config (T009 / FR-008..FR-011).

Corresponds to ``specs/003-pglite-path-backup-restore/data-model.md`` §3.
"""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from pglite_pydb.config import SidecarConfig


SIDECAR_REL = Path(".pglite-pydb") / "config.json"


def test_load_missing_returns_default_and_does_not_create_file(tmp_path: Path) -> None:
    cfg = SidecarConfig.load(tmp_path)
    assert cfg.backup_location is None
    assert cfg.schema_version == 1
    assert not (tmp_path / SIDECAR_REL).exists()
    # No sidecar subdirectory should have been created either.
    assert not (tmp_path / ".pglite-pydb").exists()


def test_save_creates_sidecar_dir_and_writes_json(tmp_path: Path) -> None:
    cfg = SidecarConfig(backup_location="/abs/backups")
    path = cfg.save(tmp_path)
    assert path == tmp_path / SIDECAR_REL
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "backup_location": "/abs/backups",
        "schema_version": 1,
    }


def test_round_trip_preserves_backup_location(tmp_path: Path) -> None:
    SidecarConfig(backup_location="/x/y/z").save(tmp_path)
    cfg = SidecarConfig.load(tmp_path)
    assert cfg.backup_location == "/x/y/z"
    assert cfg.schema_version == 1


def test_unsupported_schema_version_raises_with_upgrade_hint(
    tmp_path: Path,
) -> None:
    sidecar = tmp_path / ".pglite-pydb"
    sidecar.mkdir()
    (sidecar / "config.json").write_text(
        json.dumps({"schema_version": 999, "backup_location": None}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as excinfo:
        SidecarConfig.load(tmp_path)
    msg = str(excinfo.value)
    assert "schema_version" in msg
    assert "upgrade" in msg.lower()


def test_sidecar_json_is_utf8_indented_sorted_keys(tmp_path: Path) -> None:
    SidecarConfig(backup_location="/a/b").save(tmp_path)
    text = (tmp_path / SIDECAR_REL).read_text(encoding="utf-8")
    # Sorted keys → "backup_location" appears before "schema_version".
    assert text.index("backup_location") < text.index("schema_version")
    # 2-space indent.
    assert "\n  " in text
