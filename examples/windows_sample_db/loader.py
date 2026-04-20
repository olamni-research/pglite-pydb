"""Loader for the vendored PostgreSQLSampleDatabase dump.

Covers T008 (plus the U1 remediation of the analysis report): SHA-256
integrity check of the vendored dump, three-way data-directory status
(``fresh`` / ``warm`` / ``inconsistent``), and the entry point that
orchestrates a cold load.

Pure-stdlib; no psycopg import at module scope so the file can be
imported and exercised by unit tests that don't spin up PGlite.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


DUMP_FILENAME = "sample_db.sql"
CHECKSUM_FILENAME = "sample_db.sql.sha256"
INSTALL_MARKER = ".procedures_installed"  # written by procedures.py (Slice C)


class PgDataStatus(str, Enum):
    """Three-way status of a PGlite on-disk data directory."""

    FRESH = "fresh"                  # empty or non-existent
    WARM = "warm"                    # valid PGlite dataDir (has PG_VERSION)
    INCONSISTENT = "inconsistent"    # partial / interrupted prior run


class LoaderError(RuntimeError):
    """Base class for loader failures. Carries a suggested exit code.

    Exit codes follow contracts/cli.md.
    """

    exit_code: int = 1


class DumpIntegrityError(LoaderError):
    """The vendored dump file is missing, truncated, or has the wrong SHA-256."""

    exit_code = 3


class DataDirInconsistentError(LoaderError):
    """Data directory exists but is in a partial state (e.g. missing PG_VERSION).

    Spec edge case 'partially populated data directory' -> exit code 6.
    """

    exit_code = 6


@dataclass
class LoaderState:
    """Snapshot of loader-relevant state, captured once at process start."""

    data_dir: Path
    dump_path: Path
    checksum_path: Path
    expected_sha256: str
    dump_size: int
    pgdata_status: PgDataStatus

    @property
    def fresh_load_required(self) -> bool:
        return self.pgdata_status is PgDataStatus.FRESH

    @property
    def procedures_installed(self) -> bool:
        return (self.data_dir / INSTALL_MARKER).exists()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def default_data_root() -> Path:
    """The ``data/`` directory that holds the vendored dump + pgdata/.

    Resolved relative to this file's location so ``cd`` doesn't matter.
    """
    return (Path(__file__).resolve().parent / "data").resolve()


def compute_sha256(path: Path, _chunk: int = 1 << 20) -> str:
    """Stream-compute SHA-256 of a file without loading it all into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_chunk)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def read_expected_checksum(checksum_path: Path) -> str:
    """Read the ``.sha256`` sidecar.

    Accepts either the raw hex digest or the ``<hex>  <filename>`` form
    that ``sha256sum`` produces.
    """
    text = checksum_path.read_text(encoding="ascii").strip()
    if not text:
        raise DumpIntegrityError(f"empty checksum file: {checksum_path}")
    token = text.split()[0]
    if len(token) != 64 or not all(c in "0123456789abcdef" for c in token.lower()):
        raise DumpIntegrityError(
            f"checksum file does not contain a sha-256 hex digest: {checksum_path}"
        )
    return token.lower()


def detect_pgdata_status(data_dir: Path) -> PgDataStatus:
    """Classify the on-disk state of a PGlite data directory.

    - Nonexistent / empty      -> FRESH
    - PG_VERSION present       -> WARM
    - Anything else            -> INCONSISTENT (e.g. postmaster.opts present
                                  from an interrupted prior run)
    """
    if not data_dir.exists():
        return PgDataStatus.FRESH
    # Empty directory counts as fresh.
    try:
        if not any(data_dir.iterdir()):
            return PgDataStatus.FRESH
    except OSError:
        return PgDataStatus.INCONSISTENT

    if (data_dir / "PG_VERSION").is_file():
        return PgDataStatus.WARM
    return PgDataStatus.INCONSISTENT


def capture_state(
    data_dir: Path,
    data_root: Path | None = None,
) -> LoaderState:
    """Gather a ``LoaderState`` snapshot and verify dump integrity.

    Raises ``DumpIntegrityError`` if the dump is missing, the sidecar is
    malformed, or the checksum does not match. Does NOT itself raise on
    an inconsistent data directory -- the caller (``load_if_needed``)
    decides whether to tolerate INCONSISTENT (e.g. if ``--reset`` was
    passed).
    """
    root = (data_root or default_data_root()).resolve()
    dump_path = root / DUMP_FILENAME
    checksum_path = root / CHECKSUM_FILENAME

    if not dump_path.is_file():
        raise DumpIntegrityError(
            f"vendored dump file is missing: {dump_path} "
            f"(expected per spec FR-001)"
        )
    if not checksum_path.is_file():
        raise DumpIntegrityError(
            f"vendored checksum sidecar is missing: {checksum_path}"
        )

    expected = read_expected_checksum(checksum_path)
    actual = compute_sha256(dump_path)
    if expected != actual:
        raise DumpIntegrityError(
            "vendored dump sha-256 mismatch: "
            f"expected {expected}, got {actual} for {dump_path}"
        )

    return LoaderState(
        data_dir=data_dir.resolve(),
        dump_path=dump_path,
        checksum_path=checksum_path,
        expected_sha256=expected,
        dump_size=dump_path.stat().st_size,
        pgdata_status=detect_pgdata_status(data_dir),
    )


def ensure_loadable(state: LoaderState, *, allow_reset: bool = False) -> None:
    """Raise if the data directory is in a state ``load()`` cannot work with.

    Called after ``capture_state`` and before any PGlite startup. The
    ``allow_reset`` flag is set by the CLI's ``--reset`` handler in
    Slice C, which wipes the directory before re-entering this path.
    """
    if state.pgdata_status is PgDataStatus.INCONSISTENT and not allow_reset:
        raise DataDirInconsistentError(
            f"data directory is in a partial state: {state.data_dir} "
            "(has leftover files but no PG_VERSION). "
            "Rerun with --reset to wipe and reload."
        )
