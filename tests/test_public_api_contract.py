"""Public-API preservation contract tests.

Generated per spec 001-pglite-pydb-port, contract
``specs/001-pglite-pydb-port/contracts/public-api.md`` §8. Every test
here asserts something the rename must NOT break: class/fixture names,
distribution metadata, pytest plugin registration, config fields, and
platform-conditional error paths.

These tests run on every OS in CI. Two tests are platform-conditional
(``test_windows_rejects_explicit_unix_socket`` and the IS_WINDOWS branch
of the Node-absent error test).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import subprocess
import sys
import time
from dataclasses import fields

import pytest

from pglite_pydb._platform import IS_WINDOWS


def test_top_level_imports() -> None:
    """Contract 1: every symbol in the package's __all__ resolves."""
    from pglite_pydb import (
        AsyncpgClient,
        PGliteConfig,
        PGliteManager,
        PsycopgClient,
        get_client,
        get_default_client,
    )

    # Names must resolve to callables / classes / dataclasses.
    assert isinstance(PGliteManager, type)
    assert isinstance(PGliteConfig, type)
    assert callable(get_client)
    assert callable(get_default_client)
    # Clients may be stub classes when the optional driver is not installed,
    # but the attribute itself must exist.
    assert AsyncpgClient is not None
    assert PsycopgClient is not None


def test_version_metadata_aligned() -> None:
    """Contract 1: __version__ equals the installed distribution version."""
    import pglite_pydb

    assert pglite_pydb.__version__ == importlib.metadata.version("pglite-pydb")


def test_pytest_plugin_registered() -> None:
    """Contract 2 / FR-009: the pytest11 entry point is named ``pglite_pydb``.

    Check the installed distribution's entry-point metadata directly via
    ``importlib.metadata`` — more reliable than spawning pytest, and
    equivalent (pytest reads the same metadata at startup).
    """
    eps = importlib.metadata.entry_points(group="pytest11")
    names = {ep.name for ep in eps}
    assert "pglite_pydb" in names, (
        f"Installed distribution did not register a 'pytest11' entry point "
        f"named 'pglite_pydb'. Found: {sorted(names)}. "
        f"Check pyproject.toml [project.entry-points.pytest11]."
    )

    # And verify the entry-point target resolves to a real module.
    target = next(ep for ep in eps if ep.name == "pglite_pydb").value
    assert target == "pglite_pydb.pytest_plugin", (
        f"pytest11 entry point target is {target!r}, expected "
        f"'pglite_pydb.pytest_plugin'"
    )


def test_config_fields_preserved() -> None:
    """Contract 3: every documented PGliteConfig field exists by name."""
    from pglite_pydb import PGliteConfig

    # Build the set of field names, excluding private underscore-prefixed
    # fields (if any are added in future). Contract 3 concerns the public
    # dataclass surface only.
    names = {f.name for f in fields(PGliteConfig) if not f.name.startswith("_")}
    expected = {
        "timeout",
        "cleanup_on_exit",
        "log_level",
        "socket_path",
        "work_dir",
        "node_modules_check",
        "auto_install_deps",
        "extensions",
        "node_options",
        "use_tcp",
        "tcp_host",
        "tcp_port",
    }
    missing = expected - names
    assert not missing, f"PGliteConfig missing documented fields: {missing}"


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows-only platform gate (FR-005)")
def test_windows_rejects_explicit_unix_socket() -> None:
    """Contract 3 / FR-005: ``use_tcp=False`` on Windows raises with a clear message."""
    from pglite_pydb import PGliteConfig

    with pytest.raises(RuntimeError, match="Windows"):
        PGliteConfig(use_tcp=False)


def test_legacy_import_fails() -> None:
    """Contract 7: the old ``py_pglite`` import must NOT resolve (hard rename, Q2)."""
    # The module may leak into sys.modules from other test files in the same
    # process; to give the strict check, force reload semantics via a
    # subprocess isolated from the current interpreter state.
    result = subprocess.run(
        [sys.executable, "-c", "import py_pglite"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "py_pglite import unexpectedly succeeded after hard rename"
    assert "ModuleNotFoundError" in result.stderr or "No module named" in result.stderr


def test_node_absent_error_names_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-009 / FR-006: node-absent error names every binary variant searched.

    Monkey-patch ``shutil.which`` to simulate Node not being on PATH, call
    ``_resolve_node_bin('node')``, and assert the error message mentions
    ``'node'`` on every platform plus ``'node.cmd'`` / ``'node.exe'`` on
    Windows. Also assert the call returns (as a failure) in under 2 s
    (SC-009).
    """
    import shutil

    from pglite_pydb.manager import _resolve_node_bin

    monkeypatch.setattr(shutil, "which", lambda _name: None)

    start = time.perf_counter()
    with pytest.raises(FileNotFoundError) as excinfo:
        _resolve_node_bin("node")
    elapsed = time.perf_counter() - start

    # SC-009: <2 s, fast-fail
    assert elapsed < 2.0, f"_resolve_node_bin took {elapsed:.3f}s; SC-009 requires <2s"

    msg = str(excinfo.value)
    # Every platform: the plain name must appear in the "Searched: [...]" list
    assert "node" in msg, f"Error message did not mention 'node': {msg!r}"

    if IS_WINDOWS:
        # Windows: both the .cmd and .exe variants must be enumerated
        assert "node.cmd" in msg, (
            f"Windows error did not enumerate 'node.cmd': {msg!r}"
        )
        assert "node.exe" in msg, (
            f"Windows error did not enumerate 'node.exe': {msg!r}"
        )
