"""One-shot vendoring helper for the JannikArndt PostgreSQLSampleDatabase dump.

Re-run only when intentionally re-pinning the upstream commit SHA. Produces
sample_db.sql (concatenation of the 9 upstream files in restore.sh order) and
sample_db.sql.sha256. Requires `gh` on PATH and network access.
"""

from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

UPSTREAM_REPO = "JannikArndt/PostgreSQLSampleDatabase"
UPSTREAM_SHA = "78df7422e16f5bf8e21c84da70894b092f799e29"

ORDER = [
    "data/create.sql",
    "data/products.sql",
    "data/articles.sql",
    "data/labels.sql",
    "data/customer.sql",
    "data/address.sql",
    "data/order.sql",
    "data/order_positions.sql",
    "data/stock.sql",
]


def fetch(path: str) -> str:
    url = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{UPSTREAM_SHA}/{path}"
    with urllib.request.urlopen(url) as resp:  # noqa: S310 (fixed host)
        return resp.read().decode("utf-8")


# Upstream restore.sh loads each of the 9 per-table sub-dumps against a fresh
# database, so every sub-dump redeclares its own sequences (and the shared
# lookup tables) without DROP IF EXISTS. Naive concatenation therefore fails
# on the second CREATE SEQUENCE / CREATE TABLE. We strip the duplicates at
# vendor time so the resulting file can be replayed in one shot.
_HEADER_RE = re.compile(
    r"^--\n-- Name: ([^;]+); Type: ([A-Z][A-Z ]*); Schema: ([^;]+);[^\n]*\n--\n",
    re.MULTILINE,
)
_DEDUP_TYPES = frozenset({"SEQUENCE", "TABLE", "TYPE", "SCHEMA"})


def dedup_sections(sql: str) -> str:
    """Drop the second-and-later occurrence of any duplicated CREATE-DDL section.

    Keyed on (type, schema, name) where type is restricted to the handful
    of object kinds whose CREATE is not idempotent. Other pg_dump section
    types (``SEQUENCE SET``, ``TABLE DATA``, ``COMMENT``, ``CONSTRAINT``,
    ``DEFAULT``, ``INDEX``, …) are always preserved.
    """
    headers = list(_HEADER_RE.finditer(sql))
    if not headers:
        return sql
    out: list[str] = [sql[: headers[0].start()]]
    seen: set[tuple[str, str, str]] = set()
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(sql)
        name = m.group(1).strip()
        type_ = m.group(2).strip()
        schema = m.group(3).strip()
        key = (type_, schema, name)
        if type_ in _DEDUP_TYPES and key in seen:
            continue
        seen.add(key)
        out.append(sql[m.start() : end])
    return "".join(out)


def main() -> int:
    out_dir = Path(__file__).parent
    buf: list[str] = []
    buf.append(
        "-- ============================================================\n"
        f"-- Vendored from github.com/{UPSTREAM_REPO}\n"
        f"-- Pinned commit SHA: {UPSTREAM_SHA}\n"
        "-- Concatenation order matches upstream restore.sh.\n"
        "-- ============================================================\n\n"
    )
    for p in ORDER:
        body = fetch(p)
        buf.append(f"-- ---------- BEGIN {p} ----------\n")
        buf.append(body if body.endswith("\n") else body + "\n")
        buf.append(f"-- ---------- END   {p} ----------\n\n")

    dump = dedup_sections("".join(buf))
    dump_path = out_dir / "sample_db.sql"
    dump_path.write_text(dump, encoding="utf-8", newline="\n")
    sha = hashlib.sha256(dump.encode("utf-8")).hexdigest()
    (out_dir / "sample_db.sql.sha256").write_text(
        f"{sha}  sample_db.sql\n", encoding="utf-8", newline="\n"
    )
    size = dump_path.stat().st_size
    print(f"wrote {dump_path} ({size} bytes)")
    print(f"sha256 {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
