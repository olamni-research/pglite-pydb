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
from pglite_pydb._datadir import (
    SIDECAR_DIRNAME,
    is_completely_empty_for_full_snapshot_restore,
)
from pglite_pydb._lock import InstanceLock
from pglite_pydb._pgtools import resolve_pg_dump, resolve_psql
from pglite_pydb.cli._confirm import _confirm, _confirm_destroy
from pglite_pydb.config import PGliteConfig, SidecarConfig
from pglite_pydb.errors import (
    BackupLocationNotConfiguredError,
    BackupLocationUnavailableError,
    ContainerKindMismatchError,
    CorruptContainerError,
    NoBackupsFoundError,
    RestoreConflictError,
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

    def __init__(self, config: PGliteConfig | Path | str) -> None:
        """Initialize a BackupEngine.

        ``config`` may be a fully-validated ``PGliteConfig`` (required for
        logical backup/restore, which spawns a PGliteManager) or a bare
        data-directory path (sufficient for full-snapshot restore, which
        operates on the filesystem directly and does not need the
        PGliteConfig's strict FR-005 rejection).
        """
        if isinstance(config, (str, Path)):
            self.config: PGliteConfig | None = None
            self.data_dir: Path = Path(config).resolve(strict=False)
        else:
            self.config = config
            assert config.data_dir is not None
            self.data_dir = Path(config.data_dir)

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

    # -- restore (logical) ---------------------------------------------

    def restore_logical(
        self,
        containers: Sequence[Path | str],
        *,
        overwrite: bool = False,
        assume_yes: bool = False,
    ) -> list[Path]:
        """Restore one or more logical containers into the target (data-model §9c).

        ``containers`` may include the sentinel ``"--latest"`` which is
        resolved against the configured backup location. Each container
        is applied in its own BEGIN/COMMIT so a mid-container failure
        rolls back that container only (FR-027).
        """
        location = _resolved_backup_location(self.data_dir)

        resolved_containers = self._resolve_containers(
            containers, location, kind="logical"
        )

        # Parse & validate every manifest up-front (FR-026 — fail before
        # any target mutation).
        parsed: list[tuple[Path, dict, list[str]]] = []
        for container in resolved_containers:
            manifest, sql_entries = _read_logical_container(container)
            parsed.append((container, manifest, sql_entries))

        if not parsed:
            raise NoBackupsFoundError(location, "logical")

        # If --latest, confirm selection with the operator.
        if _needs_latest_confirmation(containers):
            latest = parsed[-1]
            prompt = (
                f"Restore logical backup {latest[0].name} "
                f"(schemas={latest[1].get('included_schemas')}) "
                f"into {self.data_dir}?"
            )
            _confirm(prompt, assume_yes=assume_yes)

        psql = resolve_psql()  # fail fast if absent

        from pglite_pydb.manager import PGliteManager

        if self.config is None:
            raise RuntimeError(
                "restore_logical requires a PGliteConfig (a bare data_dir is "
                "only supported for full-snapshot restore)."
            )
        mgr = PGliteManager(self.config)
        mgr.start()
        try:
            dsn = _dsn_dict_from_manager(mgr)
            existing = set(_list_user_schemas(dsn))
            planned: list[str] = []
            for _c, manifest, _sqls in parsed:
                for s in manifest.get("included_schemas", []):
                    if s == "*":
                        continue
                    if s not in planned:
                        planned.append(s)
            conflicts = [s for s in planned if s in existing]
            if conflicts:
                if not overwrite:
                    raise RestoreConflictError(conflicts)
                prompt = (
                    f"Overwrite existing schemas {conflicts} in "
                    f"{self.data_dir}?"
                )
                _confirm(prompt, assume_yes=assume_yes)

            applied: list[Path] = []
            for container, manifest, sql_entries in parsed:
                _apply_logical_container(
                    psql=psql,
                    dsn=dsn,
                    container=container,
                    sql_entries=sql_entries,
                    drop_first=overwrite,
                )
                applied.append(container)
        finally:
            try:
                mgr.stop()
            except Exception:  # noqa: BLE001
                pass
        return applied

    # -- restore (full snapshot) ---------------------------------------

    def restore_full_snapshot(
        self,
        container: Path | str,
        *,
        assume_yes: bool = False,
        assume_yes_destroy: bool = False,
    ) -> Path:
        """Restore a full-snapshot container into the target (data-model §9d).

        ``container`` may be the sentinel ``"--latest"`` or a path/filename.
        Raises ``ContainerKindMismatchError`` if the filename prefix does
        not match ``FULL_SNAPSHOT_*`` (FR-034).

        Returns the path of the container that was applied.
        """
        location_or_none = self._maybe_resolved_backup_location()
        resolved = _resolve_single_container(
            container, location_or_none, kind="full-snapshot"
        )

        # Validate manifest (FR-026).
        manifest = _read_full_snapshot_manifest(resolved)

        prompt1 = (
            f"Restore FULL SNAPSHOT {resolved.name} "
            f"(created_at={manifest.get('created_at')}) "
            f"into {self.data_dir}?"
        )
        _confirm(prompt1, assume_yes=assume_yes)

        target = Path(self.data_dir)
        if not is_completely_empty_for_full_snapshot_restore(target):
            prompt2 = (
                f"Target {target} is NOT empty. All non-{SIDECAR_DIRNAME} "
                "content will be destroyed."
            )
            _confirm_destroy(prompt2, assume_yes_destroy=assume_yes_destroy)

        target.mkdir(parents=True, exist_ok=True)

        # Track whether the sidecar existed BEFORE we acquired the lock
        # (acquire() will create it). FR-036: target with no sidecar
        # pre-restore has no sidecar post-restore.
        sidecar_pre_existed = (target / SIDECAR_DIRNAME).is_dir()

        lock = InstanceLock(target).acquire()
        try:
            preserved_sidecar = _stash_sidecar(target)
            top_dir = resolved.name[: -len(".tar.gz")]
            try:
                _clear_data_tree_except_sidecar(target)
                with tarfile.open(resolved, "r:gz") as tar:
                    for member in tar.getmembers():
                        if member.name == f"{top_dir}/manifest.json":
                            continue
                        prefix = f"{top_dir}/data/"
                        if not member.name.startswith(prefix):
                            continue
                        relative = member.name[len(prefix):]
                        if not relative:
                            continue
                        dest = target / relative
                        if member.isdir():
                            dest.mkdir(parents=True, exist_ok=True)
                            continue
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            continue
                        with open(dest, "wb") as out:
                            out.write(extracted.read())
            except BaseException:
                _write_failed_restore_sentinel(target)
                raise
        finally:
            lock.release()

        # Finalize sidecar state AFTER the lock is released (on Windows
        # msvcrt keeps the file around; we need the handle gone before
        # we can rmtree).
        _finalize_sidecar(
            target, preserved_sidecar, pre_existed=sidecar_pre_existed
        )

        return resolved

    # -- helpers --------------------------------------------------------

    def _maybe_resolved_backup_location(self) -> Path | None:
        """Return the configured backup location OR None if not configured.

        Full-snapshot restore only needs the location for ``--latest`` or
        for resolving a bare filename; a fully-qualified path works
        standalone.
        """
        try:
            return _resolved_backup_location(self.data_dir)
        except BackupLocationNotConfiguredError:
            return None

    def _resolve_containers(
        self,
        containers: Sequence[Path | str],
        location: Path,
        *,
        kind: str,
    ) -> list[Path]:
        """Resolve a list of container arguments to concrete paths.

        Supports the ``"--latest"`` sentinel. For logical mode the sentinel
        must be the sole entry in the list.
        """
        if any(_is_latest_sentinel(c) for c in containers):
            if len(containers) != 1:
                raise ValueError(
                    "--latest cannot be combined with explicit container names"
                )
            names = list_logical_containers(location)
            if not names:
                raise NoBackupsFoundError(location, kind)
            return [location / names[-1]]

        resolved: list[Path] = []
        for c in containers:
            p = Path(c)
            if not p.is_absolute():
                p = location / p
            if not p.exists():
                raise CorruptContainerError(p, "file does not exist")
            # Kind check by filename.
            if kind == "logical" and not _LOGICAL_RE.match(p.name):
                if _FULL_SNAPSHOT_RE.match(p.name):
                    raise ContainerKindMismatchError(
                        p, actual_kind="full-snapshot", expected_kind="logical"
                    )
                raise CorruptContainerError(
                    p, "filename does not match logical container grammar"
                )
            resolved.append(p)
        return resolved


# ---------------------------------------------------------------------------
# Restore internals
# ---------------------------------------------------------------------------


def _is_latest_sentinel(value: Path | str) -> bool:
    return isinstance(value, str) and value == "--latest"


def _needs_latest_confirmation(
    containers: Sequence[Path | str],
) -> bool:
    return any(_is_latest_sentinel(c) for c in containers)


def _resolve_single_container(
    container: Path | str,
    location: Path | None,
    *,
    kind: str,
) -> Path:
    if _is_latest_sentinel(container):
        if location is None:
            raise BackupLocationNotConfiguredError("(unknown)")
        names = list_full_snapshot_containers(location)
        if not names:
            raise NoBackupsFoundError(location, kind)
        return location / names[-1]

    p = Path(container)
    if not p.is_absolute():
        if location is None:
            raise BackupLocationNotConfiguredError("(unknown)")
        p = location / p
    if not p.exists():
        raise CorruptContainerError(p, "file does not exist")
    if kind == "full-snapshot" and not _FULL_SNAPSHOT_RE.match(p.name):
        if _LOGICAL_RE.match(p.name):
            raise ContainerKindMismatchError(
                p, actual_kind="logical", expected_kind="full-snapshot"
            )
        raise CorruptContainerError(
            p, "filename does not match full-snapshot container grammar"
        )
    return p


def _read_logical_container(container: Path) -> tuple[dict, list[str]]:
    """Return (manifest, [sql-entry-names]) for a logical container.

    Raises ``CorruptContainerError`` on any structural problem.
    """
    try:
        with tarfile.open(container, "r:gz") as tar:
            names = tar.getnames()
            manifest_member = None
            for n in names:
                if n.endswith("/manifest.json"):
                    manifest_member = n
                    break
            if manifest_member is None:
                raise CorruptContainerError(container, "no manifest.json")
            f = tar.extractfile(manifest_member)
            if f is None:
                raise CorruptContainerError(container, "manifest unreadable")
            try:
                manifest = json.loads(f.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise CorruptContainerError(
                    container, f"manifest.json not valid JSON: {exc}"
                ) from exc
    except tarfile.TarError as exc:
        raise CorruptContainerError(container, f"tar error: {exc}") from exc
    except OSError as exc:
        raise CorruptContainerError(container, f"unreadable: {exc}") from exc

    _validate_manifest(container, manifest, expected_kind="logical")
    sqls = [n for n in names if n.endswith(".sql")]
    return manifest, sqls


def _read_full_snapshot_manifest(container: Path) -> dict:
    try:
        with tarfile.open(container, "r:gz") as tar:
            manifest_member = None
            for member in tar.getmembers():
                if member.name.endswith("/manifest.json"):
                    manifest_member = member
                    break
            if manifest_member is None:
                raise CorruptContainerError(container, "no manifest.json")
            f = tar.extractfile(manifest_member)
            if f is None:
                raise CorruptContainerError(container, "manifest unreadable")
            try:
                manifest = json.loads(f.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise CorruptContainerError(
                    container, f"manifest.json not valid JSON: {exc}"
                ) from exc
    except tarfile.TarError as exc:
        raise CorruptContainerError(container, f"tar error: {exc}") from exc
    except OSError as exc:
        raise CorruptContainerError(container, f"unreadable: {exc}") from exc

    _validate_manifest(container, manifest, expected_kind="full-snapshot")
    return manifest


def _validate_manifest(
    container: Path, manifest: dict, *, expected_kind: str
) -> None:
    sv = manifest.get("schema_version")
    if sv != 1:
        raise CorruptContainerError(
            container,
            f"unsupported manifest schema_version={sv!r}; upgrade pglite-pydb.",
        )
    kind = manifest.get("kind")
    if kind != expected_kind:
        raise ContainerKindMismatchError(
            container,
            actual_kind=kind or "unknown",
            expected_kind=expected_kind,
        )


def _apply_logical_container(
    *,
    psql: str,
    dsn: dict,
    container: Path,
    sql_entries: list[str],
    drop_first: bool,
) -> None:
    """Apply a single container's SQL files inside one transaction.

    On failure the whole container rolls back (FR-027).
    """
    env = os.environ.copy()
    env["PGPASSWORD"] = dsn.get("password", "")

    # Read each .sql member into memory, concatenate, wrap in BEGIN/COMMIT.
    script_parts: list[bytes] = [b"BEGIN;\n"]
    manifest_schemas: list[str] = []
    with tarfile.open(container, "r:gz") as tar:
        # Collect schemas from manifest for the optional drop_first.
        for member in tar.getmembers():
            if member.name.endswith("/manifest.json"):
                f = tar.extractfile(member)
                if f is not None:
                    m = json.loads(f.read().decode("utf-8"))
                    manifest_schemas = [
                        s for s in m.get("included_schemas", []) if s != "*"
                    ]
                break

        if drop_first:
            for s in manifest_schemas:
                script_parts.append(
                    f'DROP SCHEMA IF EXISTS "{s}" CASCADE;\n'.encode("utf-8")
                )

        for name in sql_entries:
            member = tar.getmember(name)
            f = tar.extractfile(member)
            if f is None:
                raise CorruptContainerError(container, f"sql entry {name} unreadable")
            script_parts.append(f.read())
            if not script_parts[-1].endswith(b"\n"):
                script_parts.append(b"\n")

        script_parts.append(b"COMMIT;\n")

    script = b"".join(script_parts)

    cmd = [
        psql,
        "--host",
        dsn["host"],
        "--port",
        dsn["port"],
        "--username",
        dsn["user"],
        "--no-psqlrc",
        "--set=ON_ERROR_STOP=1",
        "--single-transaction",
        dsn["dbname"],
    ]
    proc = subprocess.run(  # nosec B603 — fixed args
        cmd, input=script, capture_output=True, env=env, check=False
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"psql failed restoring {container.name} "
            f"(exit {proc.returncode}): {stderr}"
        )


def _stash_sidecar(target: Path) -> dict[str, bytes] | None:
    """Capture the target's ``.pglite-pydb/`` contents in-memory.

    Returns ``None`` when the subtree does not exist. Otherwise returns a
    dict mapping relative path → file bytes. Directories are implied by
    file paths with ``/`` separators.
    """
    sidecar = target / SIDECAR_DIRNAME
    if not sidecar.exists():
        return None
    snapshot: dict[str, bytes] = {}
    for root, _dirs, files in os.walk(sidecar):
        for name in files:
            # Skip the instance.lock file — it's a zero-byte marker
            # that's either currently held (can't read on Windows) or
            # will be re-created on next start.
            if name == "instance.lock":
                continue
            abspath = Path(root) / name
            rel = abspath.relative_to(sidecar)
            try:
                snapshot[str(rel).replace(os.sep, "/")] = abspath.read_bytes()
            except (PermissionError, OSError):
                continue
    return snapshot


def _finalize_sidecar(
    target: Path,
    preserved: dict[str, bytes] | None,
    *,
    pre_existed: bool,
) -> None:
    """Finalize the target's ``.pglite-pydb/`` subtree after restore.

    Invariant per FR-036: if no sidecar existed before the restore
    (``pre_existed=False``) the target has no sidecar afterward either.
    Otherwise preserved files are rewritten under the sidecar directory
    and the lock-file marker is cleaned up.
    """
    import shutil

    sidecar = target / SIDECAR_DIRNAME

    if not pre_existed:
        # Remove the sidecar that InstanceLock.acquire() created solely
        # to host the lock marker; preserved is implicitly empty.
        if sidecar.exists():
            shutil.rmtree(sidecar, ignore_errors=True)
        return

    # Sidecar existed before. Rewrite preserved content exactly; clear
    # any lock marker left behind by acquire().
    sidecar.mkdir(parents=True, exist_ok=True)
    # Clear everything inside except what we explicitly restore next.
    for entry in list(sidecar.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass
    if preserved:
        for rel, data in preserved.items():
            dest = sidecar / Path(*rel.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)


def _clear_data_tree_except_sidecar(target: Path) -> None:
    """Remove every top-level entry in ``target`` except the sidecar."""
    import shutil

    if not target.exists():
        return
    for entry in target.iterdir():
        if entry.name == SIDECAR_DIRNAME:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def _write_failed_restore_sentinel(target: Path) -> None:
    """Write ``<data-dir>/.pglite-pydb/FAILED_RESTORE`` (T060/T062)."""
    try:
        sidecar = target / SIDECAR_DIRNAME
        sidecar.mkdir(parents=True, exist_ok=True)
        (sidecar / "FAILED_RESTORE").write_text(
            "Restore failed mid-extraction. Remove this file once the "
            "operator has verified the data directory or re-run restore.\n"
        )
    except OSError:
        pass


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
