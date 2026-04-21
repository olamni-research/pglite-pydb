"""argparse entry point for the `pglite-pydb` console script.

Phase 4 (US2) adds the ``config`` and ``backup`` subcommands. Phase 5
(US3) will add ``restore`` per ``specs/003-pglite-path-backup-restore/
tasks.md``. Exit-code mapping follows ``contracts/cli.md`` — see the
``_EXIT_CODES`` table below.
"""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path
from typing import Callable

from pglite_pydb.config import PGliteConfig, SidecarConfig
from pglite_pydb.errors import (
    BackupLocationNotConfiguredError,
    BackupLocationUnavailableError,
    BackupSelectorMissingError,
    ConfirmationDeclinedError,
    ConfirmationRequiredError,
    ContainerKindMismatchError,
    CorruptContainerError,
    InstanceInUseError,
    InvalidDataDirError,
    MissingDataDirError,
    NoBackupsFoundError,
    PGlitePydbError,
    RestoreConflictError,
    SchemaNotFoundError,
)


# Map each taxonomy class to its stable exit code (contracts/cli.md).
_EXIT_CODES: dict[type[BaseException], int] = {
    MissingDataDirError: 3,
    InvalidDataDirError: 4,
    InstanceInUseError: 5,
    BackupLocationNotConfiguredError: 6,
    BackupLocationUnavailableError: 7,
    SchemaNotFoundError: 8,
    NoBackupsFoundError: 9,
    BackupSelectorMissingError: 10,
    ContainerKindMismatchError: 11,
    CorruptContainerError: 12,
    RestoreConflictError: 13,
    ConfirmationRequiredError: 14,
    ConfirmationDeclinedError: 15,
}


def _exit_code_for(exc: BaseException) -> int:
    for cls, code in _EXIT_CODES.items():
        if isinstance(exc, cls):
            return code
    if isinstance(exc, PGlitePydbError):
        return 1
    return 1


def _format_error(exc: BaseException) -> str:
    return f"pglite-pydb: error: {exc}"


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pglite-pydb",
        description=(
            "Manage pglite-pydb PGlite instances: back up and restore data "
            "directories. Every subcommand requires an explicit --data-dir."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    _build_config_parser(subparsers)
    _build_backup_parser(subparsers)

    return parser


def _build_config_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "config",
        help="Inspect or mutate the per-instance sidecar configuration.",
    )
    p.add_argument("--data-dir", required=False, type=Path)
    sub = p.add_subparsers(dest="config_action", metavar="<action>")

    set_p = sub.add_parser(
        "set-backup-location",
        help="Persist backup_location into the sidecar config.",
    )
    set_p.add_argument("location", type=Path)

    sub.add_parser(
        "get-backup-location",
        help="Print the configured backup_location or '(not configured)'.",
    )
    sub.add_parser("show", help="Print the full sidecar config as JSON.")

    p.set_defaults(func=_run_config)


def _build_backup_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "backup",
        help="Produce a logical or full-snapshot backup container.",
    )
    p.add_argument("--data-dir", required=False, type=Path)

    mx = p.add_mutually_exclusive_group()
    mx.add_argument(
        "--schema",
        action="append",
        metavar="NAME",
        help="Include a single schema; repeat to list multiple.",
    )
    mx.add_argument(
        "--all",
        dest="all_schemas",
        action="store_true",
        help="Include every user schema.",
    )
    mx.add_argument(
        "--full-snapshot",
        dest="full_snapshot",
        action="store_true",
        help="Produce a full physical snapshot (no schema selection).",
    )

    p.add_argument(
        "--force-hot",
        action="store_true",
        help="Logical-only: skip the instance lock and attach to a running server.",
    )

    p.set_defaults(func=_run_backup)


# ---------------------------------------------------------------------------
# Subcommand runners
# ---------------------------------------------------------------------------


def _require_data_dir(args: argparse.Namespace) -> Path:
    if args.data_dir is None:
        raise MissingDataDirError(context="pglite-pydb")
    return Path(args.data_dir).resolve(strict=False)


def _run_config(args: argparse.Namespace) -> int:
    data_dir = _require_data_dir(args)
    action = getattr(args, "config_action", None)

    if action == "set-backup-location":
        resolved = Path(args.location).resolve(strict=False)
        sidecar = SidecarConfig.load(data_dir)
        sidecar.backup_location = str(resolved)
        sidecar.save(data_dir)
        print(str(resolved))
        return 0

    if action == "get-backup-location":
        sidecar = SidecarConfig.load(data_dir)
        print(sidecar.backup_location or "(not configured)")
        return 0

    if action == "show":
        sidecar = SidecarConfig.load(data_dir)
        payload = {
            "schema_version": sidecar.schema_version,
            "backup_location": sidecar.backup_location,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    # No action given → usage error (exit 2).
    print(
        "pglite-pydb: error: 'config' requires one of "
        "'set-backup-location', 'get-backup-location', 'show'.",
        file=sys.stderr,
    )
    return 2


def _run_backup(args: argparse.Namespace) -> int:
    # Validate flag combinations (argparse cannot express the --force-hot
    # exclusions naturally, so we enforce them here).
    from pglite_pydb.backup import BackupEngine, SchemaSelection

    schemas = args.schema or []
    if not schemas and not args.all_schemas and not args.full_snapshot:
        print(
            "pglite-pydb: error: backup requires one of --schema, --all, or --full-snapshot.",
            file=sys.stderr,
        )
        return 2
    if args.force_hot and args.full_snapshot:
        print(
            "pglite-pydb: error: --force-hot is not compatible with --full-snapshot.",
            file=sys.stderr,
        )
        return 2
    if args.force_hot and not schemas and not args.all_schemas:
        print(
            "pglite-pydb: error: --force-hot requires a logical selector "
            "(--schema or --all).",
            file=sys.stderr,
        )
        return 2

    data_dir = _require_data_dir(args)

    # Construct a minimal PGliteConfig just for the mandatory data-dir
    # contract + sidecar read. The BackupEngine owns its own PGliteManager.
    cfg = PGliteConfig(data_dir=data_dir)

    # Informational banner (stderr, per contracts/cli.md).
    sidecar = SidecarConfig.load(cfg.data_dir)
    backup_location_str = sidecar.backup_location or "(not configured)"
    print(f"pglite-pydb: instance data dir:    {cfg.data_dir}", file=sys.stderr)
    print(f"pglite-pydb: backup location:      {backup_location_str}", file=sys.stderr)

    engine = BackupEngine(cfg)
    if args.full_snapshot:
        container = engine.create_full_snapshot()
    else:
        if args.all_schemas:
            selection = SchemaSelection.all()
        elif len(schemas) == 1:
            selection = SchemaSelection.single(schemas[0])
        else:
            selection = SchemaSelection.many(schemas)
        container = engine.create_logical(selection, force_hot=args.force_hot)

    print(str(container))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is None:
        parser.error(f"unknown command: {args.command!r}")
        return 2  # unreachable

    try:
        return func(args)
    except PGlitePydbError as exc:
        print(_format_error(exc), file=sys.stderr)
        return _exit_code_for(exc)
    except Exception as exc:  # noqa: BLE001
        print(_format_error(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
