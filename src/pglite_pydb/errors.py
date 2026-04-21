"""Exception hierarchy for pglite-pydb data-dir / backup / restore flows.

Every distinct failure mode enumerated in
``specs/003-pglite-path-backup-restore/data-model.md`` §10 has a named class
here. The CLI maps each to a stable exit code per
``specs/003-pglite-path-backup-restore/contracts/cli.md``.

All messages include the offending argument / path / schema / container name
(FR-030) so operators can act without re-running with more verbosity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


class PGlitePydbError(Exception):
    """Base class for all pglite-pydb feature-003 errors."""


class MissingDataDirError(PGlitePydbError):
    """Data-directory argument was absent (FR-001, FR-028)."""

    def __init__(self, context: str = "PGliteConfig") -> None:
        super().__init__(
            f"{context}: data_dir is mandatory. Pass an explicit path "
            "(e.g. data_dir=Path('/var/lib/my-pglite')). See "
            "specs/003-pglite-path-backup-restore/spec.md FR-001."
        )


class InvalidDataDirError(PGlitePydbError):
    """Path exists but is unusable as a PGlite data directory (FR-005)."""

    def __init__(self, path: Path | str, reason: str) -> None:
        self.path = Path(path)
        self.reason = reason
        super().__init__(f"Invalid data_dir {self.path!s}: {reason}")


class InstanceInUseError(PGlitePydbError):
    """Another process already holds the instance lock (FR-006, FR-017, FR-033)."""

    def __init__(self, resolved_data_dir: Path | str) -> None:
        self.resolved_data_dir = Path(resolved_data_dir)
        super().__init__(
            f"PGlite instance at {self.resolved_data_dir!s} is already in use "
            "by another process on this host. Stop the other wrapper or use "
            "--force-hot (logical backup only)."
        )


class BackupLocationNotConfiguredError(PGlitePydbError):
    """No backup_location in the sidecar config (FR-010, FR-023)."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)
        super().__init__(
            f"No backup location configured for instance at {self.data_dir!s}. "
            f"Run: pglite-pydb config set-backup-location <path> --data-dir {self.data_dir!s}"
        )


class BackupLocationUnavailableError(PGlitePydbError):
    """Configured backup location is missing, unwritable, or disconnected (FR-011)."""

    def __init__(self, location: Path | str, reason: str) -> None:
        self.location = Path(location)
        self.reason = reason
        super().__init__(
            f"Backup location {self.location!s} is unavailable: {reason}"
        )


class SchemaNotFoundError(PGlitePydbError):
    """A named schema does not exist at dump time (FR-015)."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Schema '{name}' not found in source PGlite instance.")


class NoBackupsFoundError(PGlitePydbError):
    """--latest was requested but the scoped container list is empty (FR-023)."""

    def __init__(self, location: Path | str, kind: str) -> None:
        self.location = Path(location)
        self.kind = kind
        super().__init__(
            f"No {kind} backups found in {self.location!s}. Run `pglite-pydb backup` first."
        )


class BackupSelectorMissingError(PGlitePydbError):
    """restore invoked without a container name and without --latest (FR-020)."""

    def __init__(self) -> None:
        super().__init__(
            "restore requires either one or more container filenames or --latest."
        )


class ContainerKindMismatchError(PGlitePydbError):
    """FULL_SNAPSHOT_* passed to logical restore or vice versa (FR-034)."""

    def __init__(self, container: Path | str, actual_kind: str, expected_kind: str) -> None:
        self.container = Path(container)
        self.actual_kind = actual_kind
        self.expected_kind = expected_kind
        super().__init__(
            f"Container {self.container!s} is a {actual_kind} backup but "
            f"{expected_kind} was requested."
        )


class CorruptContainerError(PGlitePydbError):
    """Tar unreadable / manifest wrong / schema_version unknown (FR-026)."""

    def __init__(self, container: Path | str, reason: str) -> None:
        self.container = Path(container)
        self.reason = reason
        super().__init__(
            f"Container {self.container!s} is corrupt or unsupported: {reason}"
        )


class RestoreConflictError(PGlitePydbError):
    """Target already contains schemas the restore would overwrite (FR-025)."""

    def __init__(self, schemas: Iterable[str]) -> None:
        self.schemas = list(schemas)
        super().__init__(
            f"Restore target already contains schemas {self.schemas}; "
            "pass --overwrite to proceed."
        )


class ConfirmationRequiredError(PGlitePydbError):
    """Non-TTY invocation missing required --assume-yes[-destroy] (FR-022, FR-035)."""

    def __init__(self, prompt: str, flag: str = "--assume-yes") -> None:
        self.prompt = prompt
        self.flag = flag
        super().__init__(
            f"Confirmation required but stdin is not a TTY: {prompt!r}. "
            f"Pass {flag} to proceed non-interactively."
        )


class ConfirmationDeclinedError(PGlitePydbError):
    """Interactive user declined the prompt (FR-021 abort path)."""

    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        super().__init__(f"User declined confirmation: {prompt!r}")


__all__ = [
    "BackupLocationNotConfiguredError",
    "BackupLocationUnavailableError",
    "BackupSelectorMissingError",
    "ConfirmationDeclinedError",
    "ConfirmationRequiredError",
    "ContainerKindMismatchError",
    "CorruptContainerError",
    "InstanceInUseError",
    "InvalidDataDirError",
    "MissingDataDirError",
    "NoBackupsFoundError",
    "PGlitePydbError",
    "RestoreConflictError",
    "SchemaNotFoundError",
]
