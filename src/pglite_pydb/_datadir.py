"""Predicates on the filesystem state at a PGliteInstance.data_dir.

See ``specs/003-pglite-path-backup-restore/data-model.md`` §2 and research §R7.
"""

from __future__ import annotations

import os

from pathlib import Path


SIDECAR_DIRNAME = ".pglite-pydb"

# Files we ignore when assessing emptiness (research §R7). OS metadata
# plus the wrapper's own sidecar. Top-level only.
EMPTINESS_ALLOW_LIST: frozenset[str] = frozenset(
    {SIDECAR_DIRNAME, ".DS_Store", "Thumbs.db", "desktop.ini"}
)

# Markers that indicate the directory already holds a PGlite data layout.
# PGlite writes a standard PostgreSQL data directory, so `PG_VERSION` is
# the authoritative marker.
PGLITE_INSTANCE_MARKERS: frozenset[str] = frozenset({"PG_VERSION"})


def _list_entries(path: Path) -> list[str]:
    try:
        return os.listdir(path)
    except FileNotFoundError:
        return []


def is_fresh(data_dir: Path) -> bool:
    """True if the directory does not exist OR is completely empty."""
    p = Path(data_dir)
    if not p.exists():
        return True
    if not p.is_dir():
        return False
    return len(_list_entries(p)) == 0


def is_existing_pglite_instance(data_dir: Path) -> bool:
    """True if the directory contains a recognisable PGlite data layout."""
    p = Path(data_dir)
    if not p.is_dir():
        return False
    entries = set(_list_entries(p))
    return bool(entries & PGLITE_INSTANCE_MARKERS)


def is_rejectable(data_dir: Path) -> bool:
    """True if the path is not usable as a PGlite data directory.

    - Path exists as a non-directory (e.g. regular file).
    - Path is a non-empty directory without a PGlite layout AND without
      the wrapper's own sidecar (a dir containing only ``.pglite-pydb/``
      from a prior start attempt is still acceptable).
    """
    p = Path(data_dir)
    if not p.exists():
        return False
    if not p.is_dir():
        return True
    if is_fresh(p):
        return False
    if is_existing_pglite_instance(p):
        return False
    # Allow dirs whose non-allow-listed entries are empty (e.g. just the
    # sidecar from a previous aborted start).
    for name in _list_entries(p):
        if name not in EMPTINESS_ALLOW_LIST:
            return True
    return False


def is_completely_empty_for_full_snapshot_restore(data_dir: Path) -> bool:
    """True if target is empty (or contains only allow-listed entries).

    Per research §R7 — top-level scan only. Any unrecognised entry flips
    the result to False and triggers the FR-035 second confirmation.
    """
    p = Path(data_dir)
    if not p.exists():
        return True
    if not p.is_dir():
        return False
    for name in _list_entries(p):
        if name not in EMPTINESS_ALLOW_LIST:
            return False
    return True


__all__ = [
    "EMPTINESS_ALLOW_LIST",
    "PGLITE_INSTANCE_MARKERS",
    "SIDECAR_DIRNAME",
    "is_completely_empty_for_full_snapshot_restore",
    "is_existing_pglite_instance",
    "is_fresh",
    "is_rejectable",
]
