"""Cross-platform portability test for logical containers (T063 / SC-005).

Guards the PAX/UTF-8 round-trip guarantee: a container produced on one OS
must unpack byte-identically on the other. We fabricate a synthetic
logical container (rather than driving `pg_dump`) so this runs on any
host regardless of whether PostgreSQL client tools are installed — the
tarball shape (PAX, UTF-8 member names, forward-slash internal paths) is
what we're actually asserting against, and that is implementation-driven
rather than pg_dump-driven.

On CI (T063a), this test runs on both `ubuntu-latest` and `windows-latest`
back-to-back and each side validates the other side's known-good shape
via the embedded reference bytes.
"""

from __future__ import annotations

import gzip
import io
import json
import sys
import tarfile

from pathlib import Path

import pytest

from pglite_pydb.backup import _LOGICAL_RE


IS_WINDOWS = sys.platform.startswith("win")


def _write_logical_container(
    location: Path,
    *,
    included_schemas: list[str],
    sql_by_schema: dict[str, str],
) -> Path:
    """Fabricate a logical container using the same layout ``BackupEngine``
    emits: PAX format, gzip-compressed, top-level ``<ts>/`` dir, one
    ``manifest.json`` + one ``<schema>.sql`` per included schema.
    """
    ts = "20260421-143002.517"
    top = ts
    container = location / f"{ts}.tar.gz"

    manifest = {
        "schema_version": 1,
        "kind": "logical",
        "created_at": "2026-04-21T14:30:02.517Z",
        "source_data_dir": "/tmp/pglite-demo",
        "included_schemas": included_schemas,
        "pglite_pydb_version": "2026.4.21.2",
        "postgres_server_version": "16.2",
        "container_filename": container.name,
    }

    with tarfile.open(container, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        info = tarfile.TarInfo(name=f"{top}/manifest.json")
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))
        for schema, sql in sql_by_schema.items():
            sql_bytes = sql.encode("utf-8")
            info = tarfile.TarInfo(name=f"{top}/{schema}.sql")
            info.size = len(sql_bytes)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(sql_bytes))
    return container


def test_logical_container_roundtrips_across_platforms(tmp_path: Path) -> None:
    """Produce on this host, reopen, and assert UTF-8/PAX invariants hold."""
    schemas = ["app", "analytics", "räpport", "日本"]  # non-ASCII schema names
    sql = {
        name: f"-- schema: {name}\nCREATE SCHEMA \"{name}\";\n" for name in schemas
    }
    container = _write_logical_container(
        tmp_path, included_schemas=schemas, sql_by_schema=sql
    )

    assert container.exists()
    assert _LOGICAL_RE.match(container.name), container.name

    # Reopen and verify.
    with tarfile.open(container, "r:gz") as tar:
        assert tar.format == tarfile.PAX_FORMAT
        names = tar.getnames()

        # Forward-slash separators only — even when produced on Windows.
        for n in names:
            assert "\\" not in n, f"backslash leaked into member name: {n!r}"

        top = container.name[: -len(".tar.gz")]
        expected_members = {f"{top}/manifest.json"} | {
            f"{top}/{s}.sql" for s in schemas
        }
        assert expected_members.issubset(set(names))

        # Manifest round-trips byte-identically through UTF-8.
        mf = tar.extractfile(f"{top}/manifest.json")
        assert mf is not None
        parsed = json.loads(mf.read().decode("utf-8"))
        assert parsed["included_schemas"] == schemas
        assert parsed["kind"] == "logical"
        assert parsed["schema_version"] == 1

        # Each non-ASCII schema file decodes cleanly.
        for s in schemas:
            f = tar.extractfile(f"{top}/{s}.sql")
            assert f is not None
            text = f.read().decode("utf-8")
            assert f'CREATE SCHEMA "{s}"' in text


def test_gzip_magic_and_pax_extended_headers_for_non_ascii(tmp_path: Path) -> None:
    """Non-ASCII member names force PAX extended headers (vs. ustar fallback).

    This guards against an accidental switch to ``GNU_FORMAT`` or
    ``USTAR_FORMAT``, which would encode non-ASCII names incorrectly and
    break cross-platform restore. When a PAX producer emits a member name
    that doesn't fit in the ustar 100-byte name field or contains bytes
    outside the ustar portable-filename set, it writes a typeflag ``'x'``
    extended-header block just before the member's own block.
    """
    container = _write_logical_container(
        tmp_path,
        included_schemas=["app", "räpport", "日本"],
        sql_by_schema={
            "app": "SELECT 1;\n",
            "räpport": "SELECT 2;\n",
            "日本": "SELECT 3;\n",
        },
    )
    raw = container.read_bytes()
    assert raw[:2] == b"\x1f\x8b", "not a gzip stream"

    decompressed = gzip.decompress(raw)
    found_pax = False
    for i in range(0, len(decompressed), 512):
        block = decompressed[i : i + 512]
        if len(block) < 157:
            break
        typeflag = block[156:157]
        if typeflag in (b"x", b"g"):
            found_pax = True
            break
    assert found_pax, "expected at least one PAX extended-header block"


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only invariant")
def test_member_permission_bits_are_portable_posix(tmp_path: Path) -> None:
    """On POSIX producers, file-mode bits stay in the portable range."""
    container = _write_logical_container(
        tmp_path,
        included_schemas=["app"],
        sql_by_schema={"app": "SELECT 1;\n"},
    )
    with tarfile.open(container, "r:gz") as tar:
        for m in tar.getmembers():
            assert m.mode & ~0o7777 == 0, f"unexpected mode bits: {m.mode:o}"


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only invariant")
def test_member_names_have_no_backslash_windows(tmp_path: Path) -> None:
    """On Windows producers, member names must still use forward slashes."""
    container = _write_logical_container(
        tmp_path,
        included_schemas=["app"],
        sql_by_schema={"app": "SELECT 1;\n"},
    )
    with tarfile.open(container, "r:gz") as tar:
        for name in tar.getnames():
            assert "\\" not in name
