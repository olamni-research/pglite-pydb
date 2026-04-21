"""Installer and verifier for the 10 sample-DB procedures (T012).

Ships alongside the SQL files in ``sql/`` and is called once per cold load
from ``run_example.py`` / the launcher path in later slices. Idempotency is
provided by an install-marker file (``.procedures_installed``) that lives
next to ``pgdata/`` in the data directory and records the SHA-256 of the
concatenated SQL payload that was applied. Re-running against a warm
``pgdata/`` whose marker matches the current SQL payload is a no-op.

The connection object is duck-typed (psycopg 3 ``Connection`` is what
``run_example.py`` passes in) so that tests can hand in an in-memory stub.
"""

from __future__ import annotations

import hashlib

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Protocol

from examples.windows_sample_db.loader import INSTALL_MARKER


if TYPE_CHECKING:
    from collections.abc import Sequence


# 10 procedure signatures, in the order they are declared in
# sql/10_procedures.sql. Arg types use the names returned by
# pg_get_function_identity_arguments() so the verify() lookup matches
# exactly what Postgres stores.
PROCEDURE_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("get_customer_by_email",         ("text",)),
    ("list_articles_for_product",     ("integer", "integer", "integer")),
    ("count_articles_per_product",    ()),
    ("top_products_by_revenue",       ("integer",)),
    ("list_orders_for_customer",      ("integer",)),
    ("articles_in_category",          ("text",)),
    ("customer_order_report",         ("integer",)),
    ("bulk_log_articles_for_product", ("integer",)),
    ("rename_product_display_name",   ("integer", "text")),
    ("assert_product_exists",         ("integer",)),
)

PROCEDURE_COUNT = len(PROCEDURE_SIGNATURES)

SQL_FILES: tuple[str, ...] = (
    "00_schema_overlay.sql",
    "01_role.sql",
    "10_procedures.sql",
)


class _ConnLike(Protocol):
    def execute(self, query: str, params: Sequence[object] | None = ...) -> object: ...
    def commit(self) -> None: ...


class ProcedureVerifyError(RuntimeError):
    """Raised when verify() finds a missing signature or missing grant."""


@dataclass(frozen=True)
class InstallResult:
    ran: bool          # True if SQL was executed this call, False if skipped
    sql_sha256: str    # SHA-256 of the concatenated SQL payload
    procedure_count: int


def default_sql_dir() -> Path:
    return (Path(__file__).resolve().parent / "sql").resolve()


def _read_payload(sql_dir: Path) -> tuple[str, str]:
    """Read and concatenate the three SQL files; return (text, sha256)."""
    buf: list[str] = []
    for name in SQL_FILES:
        path = sql_dir / name
        buf.append(f"-- BEGIN {name}\n")
        buf.append(path.read_text(encoding="utf-8"))
        if not buf[-1].endswith("\n"):
            buf.append("\n")
    payload = "".join(buf)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return payload, digest


def install(
    conn: _ConnLike,
    data_dir: Path,
    *,
    sql_dir: Path | None = None,
) -> InstallResult:
    """Install overlay schema + role + 10 procedures.

    The ``conn`` argument is expected to be a psycopg 3 connection opened
    as the bootstrap superuser. If the install-marker already records the
    current SQL payload's SHA-256, this is a no-op and returns with
    ``ran=False``.
    """
    data_dir = Path(data_dir).resolve()
    sql_dir = (sql_dir or default_sql_dir()).resolve()
    payload, digest = _read_payload(sql_dir)

    marker = data_dir / INSTALL_MARKER
    if marker.exists():
        existing = marker.read_text(encoding="utf-8").strip().splitlines()
        for line in existing:
            if line.startswith("sha256="):
                if line.split("=", 1)[1].strip() == digest:
                    return InstallResult(False, digest, PROCEDURE_COUNT)
                break  # sha mismatch -> re-run

    conn.execute(payload)
    conn.commit()

    data_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        f"procedures={PROCEDURE_COUNT}\nsha256={digest}\n",
        encoding="utf-8",
    )
    return InstallResult(True, digest, PROCEDURE_COUNT)


def verify(conn: _ConnLike, *, role: str = "example_user") -> None:
    """Confirm all 10 function signatures exist and are EXECUTE-able by *role*.

    Raises ``ProcedureVerifyError`` on the first problem found.
    """
    for proc_name, arg_types in PROCEDURE_SIGNATURES:
        arg_sig = ", ".join(arg_types)
        cur = conn.execute(
            "SELECT p.oid "
            "FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' "
            "  AND p.proname = %s "
            "  AND pg_get_function_identity_arguments(p.oid) = %s",
            (proc_name, arg_sig),
        )
        row = cur.fetchone()
        if row is None:
            raise ProcedureVerifyError(
                f"procedure missing from pg_proc: public.{proc_name}({arg_sig})"
            )
        oid = row[0]
        cur = conn.execute(
            "SELECT has_function_privilege(%s, %s::oid, 'EXECUTE')",
            (role, oid),
        )
        row = cur.fetchone()
        if not row or not bool(row[0]):
            raise ProcedureVerifyError(
                f"role {role!r} lacks EXECUTE on "
                f"public.{proc_name}({arg_sig}) (oid={oid})"
            )
