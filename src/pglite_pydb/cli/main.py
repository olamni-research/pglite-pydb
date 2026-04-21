"""argparse entry point for the `pglite-pydb` console script.

Phase 1 scaffolding: prints a top-level usage message and exits with status 2
when invoked without a subcommand. Real subcommand logic (`backup`, `restore`,
`config`) lands in Phase 4 (US2) and Phase 5 (US3) per
`specs/003-pglite-path-backup-restore/tasks.md`.
"""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pglite-pydb",
        description=(
            "Manage pglite-pydb PGlite instances: back up and restore data "
            "directories. Every subcommand requires an explicit --data-dir."
        ),
    )
    parser.add_subparsers(dest="command", metavar="<command>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    # Subcommands not yet implemented in Phase 1 scaffolding.
    parser.error(f"unknown command: {args.command!r}")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    raise SystemExit(main())
