"""Backup and restore engine for pglite-pydb instances.

Phase 4 (US2) lands ``BackupEngine.create_logical`` and
``BackupEngine.create_full_snapshot``. Phase 5 (US3) will extend this
module with ``restore_logical`` / ``restore_full_snapshot`` per
``specs/003-pglite-path-backup-restore/tasks.md``.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess  # nosec B404 — used solely to invoke pg_dump/psql
import tarfile

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from pglite_pydb import __version__
from pglite_pydb._datadir import SIDECAR_DIRNAME
from pglite_pydb._lock import InstanceLock
from pglite_pydb._pgtools import resolve_pg_dump
from pglite_pydb.config import PGliteConfig, SidecarConfig
from pglite_pydb.errors import (
    BackupLocationNotConfiguredError,
    BackupLocationUnavailableError,
    SchemaNotFoundError,
)
from pglite_pydb.utils import disambiguate_filename, utc_timestamp_filename


_logger = logging.getLogger(__name__)

# System schemas excluded from `--all` (data-model §8).
_SYSTEM_SCHEMAS: frozenset[str] = frozenset(
    {"pg_catalog", "information_schema", "pg_toast"}
)
# PGlite's own internal schemas — also excluded from `--all` per §8.
_PGLITE_INTERNAL_SCHEMAS: frozenset[str] = frozenset({"pg_temp_1", "pg_toast_temp_1"})

_LOGICAL_RE = re.compile(r"^(?P<ts>\d{8}-\d{6}\.\d{3})(_\d+)?\.tar\.gz$")
_FULL_SNAPSHOT_RE = re.compile(
    r"^FULL_SNAPSHOT_(?P<ts>\d{8}-\d{6}\.\d{3})(_\d+)?\.tar\.gz$"
)


# ---------------------------------------------------------------------------
# Schema selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaSelection:
    """Backup selection mode (data-model §8)."""

    names: tuple[str, ...] = ()
    all_schemas: bool = False

    @classmethod
    def single(cls, name: str) -> "SchemaSelection":
        return cls(names=(name,), all_schemas=False)

    @classmethod
    def many(cls, names: Sequence[str]) -> "SchemaSelection":
        # Dedup preserving first-seen order.
        seen: list[str] = []
        for n in names:
            if n not in seen:
                seen.append(n)
        return cls(names=tuple(seen), all_schemas=False)

    @classmethod
    def all(cls) -> "SchemaSelection":
        return cls(names=(), all_schemas=True)

    @property
    def manifest_value(self) -> list[str]:
        """Value to embed in ``manifest.included_schemas``."""
        if self.all_schemas:
            return ["*"]
        return list(self.names)


# ---------------------------------------------------------------------------
# Backup location helpers
# ---------------------------------------------------------------------------


def _resolved_backup_location(data_dir: Path) -> Path:
    """Load the sidecar config and return the configured backup location.

    Raises ``BackupLocationNotConfiguredError`` when the sidecar has no
    ``backup_location`` set (FR-010).
    """
    cfg = SidecarConfig.load(data_dir)
    if not cfg.backup_location:
        raise BackupLocationNotConfiguredError(data_dir)
    return Path(cfg.backup_location)


def _assert_location_writable(location: Path) -> None:
    if not location.exists():
        raise BackupLocationUnavailableError(location, "directory does not exist")
    if not location.is_dir():
        raise BackupLocationUnavailableError(location, "path is not a directory")
    if not os.access(location, os.W_OK):
        raise BackupLocationUnavailableError(location, "directory is not writable")


def list_logical_containers(location: Path) -> list[str]:
    """Return logical container filenames, sorted lexically ascending."""
    if not location.is_dir():
        return []
    return sorted(n for n in os.listdir(location) if _LOGICAL_RE.match(n))


def list_full_snapshot_containers(location: Path) -> list[str]:
    """Return full-snapshot container filenames, sorted lexically ascending."""
    if not location.is_dir():
        return []
    return sorted(n for n in os.listdir(location) if _FULL_SNAPSHOT_RE.match(n))


# ---------------------------------------------------------------------------
# Full-snapshot helper (tar walk that excludes the sidecar subtree)
# ---------------------------------------------------------------------------


def _iter_snapshot_entries(data_dir: Path) -> Iterator[Path]:
    """Yield every file/dir under ``data_dir`` EXCEPT the sidecar subtree.

    Top-level ``.pglite-pydb/`` is excluded per FR-032. Symlinks and
    special files are copied as-is (stdlib tarfile.add handles those).
    """
    for entry in sorted(data_dir.iterdir()):
        if entry.name == SIDECAR_DIRNAME:
            continue
        yield entry


# ---------------------------------------------------------------------------
# BackupEngine
# ---------------------------------------------------------------------------


class BackupEngine:
    """Logical + full-snapshot backup operations (data-model §9)."""

    def __init__(self, config: PGliteConfig) -> None:
        self.config = config
        assert config.data_dir is not None
        self.data_dir: Path = Path(config.data_dir)

    # -- logical --------------------------------------------------------

    def create_logical(
        self,
        selection: SchemaSelection,
        *,
        force_hot: bool = False,
    ) -> Path:
        """Produce a logical ``<ts>.tar.gz`` container (data-model §9a).

        Starts a PGlite TCP server under the FR-006 instance lock by
        default. ``force_hot=True`` skips the lock and attaches to an
        already-running server (FR-017's best-effort path).

        Returns the absolute path of the finalised container.
        """
        location = _resolved_backup_location(self.data_dir)
        _assert_location_writable(location)
        pg_dump = resolve_pg_dump()  # fail fast if absent

        # Start (or attach to) a PGlite server.
        from pglite_pydb.manager import PGliteManager

        mgr: PGliteManager | None = None
        lock: InstanceLock | None = None
        if force_hot:
            # Attach to a foreign running server — no lock, no manager spawn.
            # We still need connection info; rely on the caller's config.
            dsn_dict = _dsn_dict_from_config(self.config)
        else:
            mgr = PGliteManager(self.config)
            mgr.start()
            dsn_dict = _dsn_dict_from_manager(mgr)

        try:
            # Resolve schema list against the live catalog.
            if selection.all_schemas:
                schemas = _list_user_schemas(dsn_dict)
            else:
                existing = set(_list_user_schemas(dsn_dict))
                for name in selection.names:
                    if name not in existing:
                        raise SchemaNotFoundError(name)
                schemas = list(selection.names)

            postgres_version = _query_postgres_version(dsn_dict)

            ts = utc_timestamp_filename()
            existing_containers = list_logical_containers(location)
            container_filename = disambiguate_filename(
                f"{ts}.tar.gz", existing_containers
            )
            final_path = location / container_filename
            partial_path = location / (container_filename + ".partial")

            created_at_iso = _iso_from_ts(ts)
            top_dir = container_filename[: -len(".tar.gz")]

            try:
                with tarfile.open(
                    partial_path, mode="w:gz", format=tarfile.PAX_FORMAT
                ) as tar:
                    # Each schema → `<top_dir>/<schema>.sql`
                    for schema in schemas:
                        sql_bytes = _pg_dump_schema(pg_dump, dsn_dict, schema)
                        _add_bytes(tar, f"{top_dir}/{schema}.sql", sql_bytes)

                    manifest = {
                        "schema_version": 1,
                        "kind": "logical",
                        "created_at": created_at_iso,
                        "source_data_dir": str(self.data_dir),
                        "included_schemas": selection.manifest_value,
                        "pglite_pydb_version": __version__,
                        "postgres_server_version": postgres_version,
                        "container_filename": container_filename,
                    }
                    manifest_bytes = _json_bytes(manifest)
                    _add_bytes(tar, f"{top_dir}/manifest.json", manifest_bytes)

                os.replace(partial_path, final_path)
            except BaseException:
                # Cleanup any partial on any failure (FR-017).
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except OSError:
                    pass
                raise
        finally:
            if mgr is not None:
                try:
                    mgr.stop()
                except Exception:  # noqa: BLE001
                    pass

        return final_path

    # -- full snapshot --------------------------------------------------

    def create_full_snapshot(self) -> Path:
        """Produce a ``FULL_SNAPSHOT_<ts>.tar.gz`` container (data-model §9b).

        Always acquires the FR-006 instance lock; there is no ``force_hot``
        counterpart because full-snapshots are physical and cannot tolerate
        a concurrent writer (FR-033). Target is **the wrapped data
        directory as-is** — no server is started; we just archive the file
        tree with the sidecar excluded per FR-032.
        """
        location = _resolved_backup_location(self.data_dir)
        _assert_location_writable(location)

        ts = utc_timestamp_filename()
        existing = list_full_snapshot_containers(location)
        container_filename = disambiguate_filename(
            f"FULL_SNAPSHOT_{ts}.tar.gz", existing
        )
        final_path = location / container_filename
        partial_path = location / (container_filename + ".partial")

        created_at_iso = _iso_from_ts(ts)
        top_dir = container_filename[: -len(".tar.gz")]

        lock = InstanceLock(self.data_dir).acquire()
        try:
            try:
                with tarfile.open(
                    partial_path, mode="w:gz", format=tarfile.PAX_FORMAT
                ) as tar:
                    # Walk the data dir; skip the sidecar subtree (FR-032).
                    for entry in _iter_snapshot_entries(self.data_dir):
                        arcname = f"{top_dir}/data/{entry.name}"
                        tar.add(entry, arcname=arcname, recursive=True)

                    manifest = {
                        "schema_version": 1,
                        "kind": "full-snapshot",
                        "created_at": created_at_iso,
                        "source_data_dir": str(self.data_dir),
                        "pglite_pydb_version": __version__,
                        "postgres_server_version": _best_effort_pg_version(
                            self.data_dir
                        ),
                        "container_filename": container_filename,
                    }
                    manifest_bytes = _json_bytes(manifest)
                    _add_bytes(tar, f"{top_dir}/manifest.json", manifest_bytes)

                os.replace(partial_path, final_path)
            except BaseException:
                try:
                    if partial_path.exists():
                        partial_path.unlink()
                except OSError:
                    pass
                raise
        finally:
            lock.release()

        return final_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_from_ts(ts: str) -> str:
    """Convert ``YYYYMMDD-HHMMSS.fff`` → ISO-8601 ``...Z`` string."""
    # ts = "20260421-143002.517"
    date, time = ts.split("-")
    hms, ms = time.split(".")
    return (
        f"{date[0:4]}-{date[4:6]}-{date[6:8]}T"
        f"{hms[0:2]}:{hms[2:4]}:{hms[4:6]}.{ms}Z"
    )


def _json_bytes(obj: dict) -> bytes:
    return (
        json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _dsn_dict_from_manager(mgr) -> dict:  # type: ignore[no-untyped-def]
    """Extract a libpq env dict from a live ``PGliteManager``."""
    cfg = mgr.config
    host = cfg.tcp_host if cfg.use_tcp else str(Path(cfg.socket_path).parent)
    port = mgr.resolved_port or cfg.tcp_port or 5432
    return {
        "host": host,
        "port": str(port),
        "user": "postgres",
        "password": "postgres",
        "dbname": "postgres",
    }


def _dsn_dict_from_config(cfg: PGliteConfig) -> dict:
    host = cfg.tcp_host if cfg.use_tcp else str(Path(cfg.socket_path).parent)
    port = cfg.tcp_port or 5432
    return {
        "host": host,
        "port": str(port),
        "user": "postgres",
        "password": "postgres",
        "dbname": "postgres",
    }


def _list_user_schemas(dsn: dict) -> list[str]:
    """Return user schemas excluding the system / pglite internals list."""
    import psycopg

    conninfo = _psycopg_conninfo(dsn)
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "ORDER BY schema_name"
            )
            rows = [r[0] for r in cur.fetchall()]
    return [
        s
        for s in rows
        if s not in _SYSTEM_SCHEMAS
        and s not in _PGLITE_INTERNAL_SCHEMAS
        and not s.startswith("pg_")
    ]


def _query_postgres_version(dsn: dict) -> str:
    import psycopg

    conninfo = _psycopg_conninfo(dsn)
    with psycopg.connect(conninfo) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            row = cur.fetchone()
            return row[0] if row else "unknown"


def _psycopg_conninfo(dsn: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in dsn.items())


def _pg_dump_schema(pg_dump: str, dsn: dict, schema: str) -> bytes:
    """Run ``pg_dump --schema=<name>`` and return the stdout bytes."""
    env = os.environ.copy()
    env["PGPASSWORD"] = dsn.get("password", "")
    cmd = [
        pg_dump,
        "--host",
        dsn["host"],
        "--port",
        dsn["port"],
        "--username",
        dsn["user"],
        "--format=plain",
        "--no-owner",
        "--no-privileges",
        "--schema",
        schema,
        dsn["dbname"],
    ]
    proc = subprocess.run(  # nosec B603 — fixed args, trusted pg_dump
        cmd, capture_output=True, env=env, check=False
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"pg_dump failed for schema {schema!r} (exit {proc.returncode}): {stderr}"
        )
    return proc.stdout


def _best_effort_pg_version(data_dir: Path) -> str:
    """Read ``PG_VERSION`` from the data dir; fall back to unknown."""
    pg_version = data_dir / "PG_VERSION"
    if pg_version.exists():
        try:
            return f"PGlite data at PG_VERSION {pg_version.read_text().strip()}"
        except OSError:
            pass
    return "unknown"


__all__ = [
    "BackupEngine",
    "SchemaSelection",
    "list_full_snapshot_containers",
    "list_logical_containers",
]
