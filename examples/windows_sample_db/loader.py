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
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


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


_COPY_HEADER_RE = re.compile(
    r"^\s*COPY\s+(\S+)\s*(?:\(([^)]*)\))?\s+FROM\s+stdin\s*;?\s*$",
    re.IGNORECASE,
)

# pg_dump TSV escape set per PostgreSQL text-format COPY rules.
_TSV_ESCAPES = {
    "t": "\t", "n": "\n", "r": "\r",
    "\\": "\\", "b": "\b", "f": "\f", "v": "\v",
}


def _decode_tsv_field(raw: str) -> Any:
    """Decode one COPY-text field to a Python value.

    ``\\N`` → None; standard C-style escapes for tab/newline/etc. are
    unescaped. Anything else is returned as a (decoded) string; psycopg's
    adapter turns it into the right SQL type via the target column.
    """
    if raw == r"\N":
        return None
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            out.append(_TSV_ESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _flush_copy_block(conn: Any, copy_cmd: str, data_lines: list[str]) -> None:
    """Rewrite one COPY block as an ``INSERT`` batch and execute it.

    PGlite's ``execProtocolRaw`` path crashes when fed a stateful COPY
    frontend sequence (Query → CopyData* → CopyDone) across separate
    calls, so we avoid the COPY protocol entirely at load time.
    """
    m = _COPY_HEADER_RE.match(copy_cmd)
    if not m:
        raise DumpIntegrityError(f"unparseable COPY header: {copy_cmd!r}")
    table = m.group(1)
    cols_raw = m.group(2)
    col_list_sql = f" ({cols_raw})" if cols_raw else ""

    rows: list[tuple[Any, ...]] = []
    for line in data_lines:
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        fields = stripped.split("\t")
        rows.append(tuple(_decode_tsv_field(f) for f in fields))

    if not rows:
        return

    ncols = len(rows[0])
    placeholders = "(" + ",".join(["%s"] * ncols) + ")"
    sql = f"INSERT INTO {table}{col_list_sql} VALUES {placeholders}"
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def _split_statements(sql: str) -> list[str]:
    """Split a ``pg_dump`` SQL batch into individual statements.

    Statement boundary = a line whose rightmost non-whitespace char is
    ``;``. This is safe for pg_dump text output, which escapes embedded
    semicolons inside string literals via doubled quotes and never emits
    bare ``;`` inside a string at end-of-line.
    """
    out: list[str] = []
    current: list[str] = []
    for line in sql.splitlines(keepends=True):
        current.append(line)
        if line.rstrip().endswith(";"):
            stmt = "".join(current).strip()
            if stmt and not all(
                (not ln.strip()) or ln.lstrip().startswith("--")
                for ln in current
            ):
                out.append(stmt)
            current = []
    tail = "".join(current).strip()
    if tail:
        out.append(tail)
    return out


_FK_STMT_RE = re.compile(
    r"\bALTER\s+TABLE\b[\s\S]*?\bADD\s+CONSTRAINT\b[\s\S]*?\bFOREIGN\s+KEY\b",
    re.IGNORECASE,
)


def load_dump_with_copy(conn: Any, dump_path: Path) -> None:
    """Execute a ``pg_dump`` plain-text dump against ``conn``.

    The vendored dump uses ``COPY … FROM stdin`` blocks terminated by
    ``\\.``. This helper streams the file, executes each non-COPY
    statement individually via ``conn.execute`` (under autocommit so a
    single failure doesn't roll back an entire multi-statement batch),
    and rewrites each COPY block as a batched INSERT.

    Cross-table FOREIGN KEY constraints are deferred to the end of the
    load because the vendored dump concatenates multiple pg_dump
    sub-dumps (one per table) in the upstream's ``restore.sh`` order —
    early sub-dumps declare FKs that reference tables created by later
    sub-dumps, so applying them eagerly raises ``UndefinedTable``.
    """
    sql_buf: list[str] = []
    in_copy = False
    copy_cmd: str | None = None
    copy_data: list[str] = []
    deferred_fks: list[str] = []

    def _flush_sql() -> None:
        if not sql_buf:
            return
        text = "".join(sql_buf)
        sql_buf.clear()
        for stmt in _split_statements(text):
            if _FK_STMT_RE.search(stmt):
                deferred_fks.append(stmt)
                continue
            conn.execute(stmt)

    prior_autocommit = getattr(conn, "autocommit", False)
    conn.autocommit = True
    try:
        with dump_path.open("r", encoding="utf-8", newline="") as f:
            for line in f:
                if in_copy:
                    if line.rstrip("\r\n") == r"\.":
                        assert copy_cmd is not None
                        _flush_copy_block(conn, copy_cmd, copy_data)
                        in_copy = False
                        copy_cmd = None
                        copy_data = []
                    else:
                        copy_data.append(line)
                    continue

                stripped = line.lstrip()
                if (
                    stripped[:5].upper() == "COPY "
                    and " FROM stdin" in line
                    and line.rstrip().endswith(";")
                ):
                    _flush_sql()
                    copy_cmd = line.strip().rstrip(";")
                    in_copy = True
                else:
                    sql_buf.append(line)

        if in_copy:
            raise DumpIntegrityError(
                f"unterminated COPY block in {dump_path} "
                f"(missing trailing '\\.' marker)"
            )
        _flush_sql()
        # Apply the deferred FK constraints now that all referenced
        # tables have been created and populated.
        for stmt in deferred_fks:
            conn.execute(stmt)
        # The dump mutates session ``search_path`` via ``SET search_path = ''``
        # in every sub-dump; restore a sane default for later callers.
        conn.execute("SET search_path TO public")
    finally:
        conn.autocommit = prior_autocommit


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
