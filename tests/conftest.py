"""Shared pytest configuration for the pglite-pydb test suite.

Registers skip rules for the feature-003 markers so tests relying on
external tooling (`pg_dump`) are quietly skipped when the prerequisite is
absent, rather than erroring out.
"""

from __future__ import annotations

import shutil

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    skip_no_pg_dump = pytest.mark.skip(
        reason="requires pg_dump on PATH (PostgreSQL 15+ client tools)",
    )
    pg_dump_available = shutil.which("pg_dump") is not None
    for item in items:
        if "requires_pg_dump" in item.keywords and not pg_dump_available:
            item.add_marker(skip_no_pg_dump)
