"""Locate the PostgreSQL client binaries required by the backup engine.

Both ``pg_dump`` (T038) and ``psql`` (T048/T059) are external pre-requisites
(PostgreSQL 15+ client tools). Operators may override the discovered path
via ``PGLITE_PYDB_PG_DUMP`` / ``PGLITE_PYDB_PSQL`` environment variables.
Absence of either is a fail-fast runtime error — the CLI maps it to the
generic exit code 1 with the actionable message documented in
``specs/003-pglite-path-backup-restore/contracts/cli.md``.
"""

from __future__ import annotations

import os
import shutil


class MissingPostgresClientError(RuntimeError):
    """Raised when ``pg_dump`` or ``psql`` cannot be located on PATH."""


def _resolve(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        if shutil.which(override) or os.path.isfile(override):
            return override
        raise MissingPostgresClientError(
            f"{env_var}={override!r} does not point at an executable."
        )
    found = shutil.which(name)
    if found:
        return found
    raise MissingPostgresClientError(
        f"{name} not found on PATH; install PostgreSQL 15+ client tools "
        f"or set ${env_var} to an explicit path."
    )


def resolve_pg_dump() -> str:
    """Return an absolute path to ``pg_dump`` or raise."""
    return _resolve("pg_dump", "PGLITE_PYDB_PG_DUMP")


def resolve_psql() -> str:
    """Return an absolute path to ``psql`` or raise."""
    return _resolve("psql", "PGLITE_PYDB_PSQL")


__all__ = [
    "MissingPostgresClientError",
    "resolve_pg_dump",
    "resolve_psql",
]
