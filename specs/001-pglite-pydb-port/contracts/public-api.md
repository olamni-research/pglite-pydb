# Public API Preservation Contract

**Feature**: 001-pglite-pydb-port · **Spec**: [../spec.md](../spec.md) · **Plan**: [../plan.md](../plan.md)

This document enumerates every surface that MUST survive the rename with its shape intact. The rename is a hard one (Q2 clarification: no back-compat shim), so while the *names of the owning modules change*, the *shape of each exported symbol does not*.

FR-002 is the governing requirement: "The refactor MUST preserve existing public API shapes — class names, fixture names, configuration field names, and exception types — so that a user upgrading only rewrites `py_pglite` → `pglite_pydb` in imports."

## Contract conformance test (Step 12 verification)

After the refactor, the following must all hold on a freshly-installed `pglite-pydb` wheel:

```python
# Core
from pglite_pydb import (
    AsyncpgClient,
    PGliteConfig,
    PGliteManager,
    PsycopgClient,
    get_client,
    get_default_client,
)

# Django
from pglite_pydb.django import db, transactional_db                      # fixtures
from pglite_pydb.django.backend.base import DatabaseWrapper              # Django backend

# SQLAlchemy
from pglite_pydb.sqlalchemy import pglite_session, pglite_engine          # fixtures (names exact)

# Version + metadata
import pglite_pydb
assert pglite_pydb.__version__.startswith(("0.6", "0.7", "1."))           # post-port versions only
```

No `ImportError`, no `AttributeError`.

## Contract 1 — Top-level package exports

**Source** (preserved, module path only changes): `src/pglite_pydb/__init__.py`

Every symbol in the current `__all__` tuple MUST be re-exported from `pglite_pydb`:

| Symbol name            | Kind      | Owning module (after rename)              | Signature preservation requirement |
|------------------------|-----------|--------------------------------------------|------------------------------------|
| `AsyncpgClient`        | class     | `pglite_pydb.clients`                      | identical constructor & method set |
| `PsycopgClient`        | class     | `pglite_pydb.clients`                      | identical constructor & method set |
| `get_client`           | function  | `pglite_pydb.clients`                      | identical argspec                   |
| `get_default_client`   | function  | `pglite_pydb.clients`                      | identical argspec                   |
| `PGliteConfig`         | dataclass | `pglite_pydb.config`                       | all existing fields preserved (see Contract 3) |
| `PGliteManager`        | class     | `pglite_pydb.manager`                      | all existing public methods preserved (see Contract 4) |

**`pglite_pydb.__version__`**: MUST exist (read via `importlib.metadata.version(__name__)`, same pattern as today). MUST return a string that matches the distribution's version.

## Contract 2 — pytest plugin

**Entry-point name**: `pglite_pydb` (in `pyproject.toml` `[project.entry-points.pytest11]`).

**FR-009** compliance check: after `pip install pglite-pydb`, `pytest --trace-config` MUST list `pglite_pydb` among the registered plugins.

**Fixtures** the plugin auto-discovers (names unchanged):

- `pglite_engine` (SQLAlchemy-style engine fixture)
- `pglite_session`
- `pglite_config`
- `pglite_manager`
- Any other fixture currently declared under `src/py_pglite/fixtures.py` or `src/py_pglite/*/fixtures.py`

**Rule**: if a user's existing `conftest.py` has `pytest_plugins = ["py_pglite.pytest_plugin"]`, they MUST update that string to `"pglite_pydb.pytest_plugin"`. No alias is provided.

## Contract 3 — `PGliteConfig` dataclass

All fields preserved by name, type, and default — with one platform-conditional nuance captured in the refactor:

| Field                  | Type                | Default (pre-rename)                    | Default (post-rename)                                       | Notes |
|------------------------|---------------------|-----------------------------------------|-------------------------------------------------------------|-------|
| `timeout`              | `int`               | `30`                                     | `30`                                                        | unchanged |
| `cleanup_on_exit`      | `bool`              | `True`                                   | `True`                                                      | unchanged |
| `log_level`            | `str`               | `"INFO"`                                  | `"INFO"`                                                    | unchanged |
| `socket_path`          | `str`               | `_get_secure_socket_path()`             | same (Linux/macOS); ignored on Windows (validation in `__post_init__`) | unchanged default factory |
| `work_dir`             | `Path \| None`      | `None`                                   | `None`                                                      | unchanged |
| `node_modules_check`   | `bool`              | `True`                                   | `True`                                                      | unchanged |
| `auto_install_deps`    | `bool`              | `True`                                   | `True`                                                      | unchanged |
| `extensions`           | `list[str] \| None` | `None`                                   | `None`                                                      | unchanged |
| `node_options`         | `str \| None`       | `None`                                   | `None`                                                      | unchanged |
| `use_tcp`              | `bool`              | `False`                                   | `False` on user-facing default; **auto-promoted to `True` by `__post_init__` when `sys.platform == "win32"` and the user did not pass an explicit value** | key platform branch; documented in R2 of research.md |
| `tcp_host`             | `str`               | `"127.0.0.1"`                            | `"127.0.0.1"`                                               | unchanged |
| `tcp_port`             | `int`               | `5432`                                   | `5432` unless `__post_init__` promotes to `0` on Windows (OS-assigned ephemeral, per R4) | key platform branch |

**Invariant** (new): when `use_tcp == False` and `IS_WINDOWS == True`, `__post_init__` MUST raise `RuntimeError` with a message naming the platform and suggesting removal of the explicit override (FR-005).

**Invariant** (preserved): every existing validation in `__post_init__` (timeout positivity, log-level whitelist, extensions whitelist, TCP port range, TCP host non-empty) stays.

**`get_connection_string` method**: must continue to emit a URI usable by `psycopg` and `SQLAlchemy` on every supported OS. The body may branch on `IS_WINDOWS` to emit `host=127.0.0.1 port=<resolved>` rather than `host=<socket_path>`, but the method signature and return type MUST NOT change.

## Contract 4 — `PGliteManager` class

The class name, constructor argspec, and public method names MUST be preserved.

Public API contract (minimum, expanded as needed after reading the current `manager.py`):

| Member             | Kind      | Preservation requirement                           |
|--------------------|-----------|----------------------------------------------------|
| `__init__(config)` | method    | constructor takes a single `PGliteConfig` argument |
| `start()`          | method    | idempotent startup; returns `None`; raises on failure |
| `stop()`           | method    | graceful shutdown then forceful (see R3)           |
| `__enter__` / `__exit__` | method | context-manager protocol preserved              |
| `is_running` / `is_ready` | property | boolean health check                            |
| `connection_string` | property | resolved connection string (per Contract 3)        |

**Private additions** (internal, not part of the contract, listed here to avoid name collisions during the refactor):

- `_resolve_node_bin(name: str) -> str` — Step 8 helper.
- `_terminate_process_tree(proc) -> None` — Step 9 helper.

## Contract 5 — Django integration

All exports from `pglite_pydb.django.*` preserved by name:

- `pglite_pydb.django.db` (fixture)
- `pglite_pydb.django.transactional_db` (fixture)
- `pglite_pydb.django.backend.base.DatabaseWrapper` (Django backend class)
- Any helper in `pglite_pydb.django.utils` that is currently publicly documented

The `ENGINE` setting in Django users' `DATABASES` config changes from `"py_pglite.django.backend"` to `"pglite_pydb.django.backend"`. This is a user-side string change, not an API shape change — it's the direct consequence of the package rename and is covered by the deprecation pointer.

## Contract 6 — SQLAlchemy integration

All exports from `pglite_pydb.sqlalchemy.*` preserved by name:

- `pglite_pydb.sqlalchemy.pglite_session` (fixture / helper)
- `pglite_pydb.sqlalchemy.pglite_engine` (fixture / helper)
- `pglite_pydb.sqlalchemy.manager.*`
- `pglite_pydb.sqlalchemy.manager_async.*`

## Contract 7 — Exception types

Any exception class currently exported (directly or transitively) via `py_pglite` or its subpackages MUST be preserved by name and inheritance chain under `pglite_pydb`. Users who catch `py_pglite.X` in their `except` clauses MUST get the same matching behaviour after renaming the import.

**New exceptions introduced by the refactor** (internal; may propagate to users):

- `FileNotFoundError` raised by `_resolve_node_bin` when Node/npm missing (stdlib exception; no new class).
- `RuntimeError` raised by `PGliteConfig.__post_init__` when `use_tcp=False` on Windows (stdlib exception; no new class).

## Contract 8 — Conformance checklist (runnable)

The Step 12 verification job MUST execute the following (shown here as pseudocode; actual implementation will live in `tests/test_public_api_contract.py` created during the refactor):

```python
def test_top_level_imports():
    from pglite_pydb import (
        AsyncpgClient, PGliteConfig, PGliteManager, PsycopgClient,
        get_client, get_default_client,
    )

def test_version_metadata_aligned():
    import pglite_pydb, importlib.metadata
    assert pglite_pydb.__version__ == importlib.metadata.version("pglite-pydb")

def test_pytest_plugin_registered():
    import subprocess, sys
    out = subprocess.check_output([sys.executable, "-m", "pytest", "--trace-config"], text=True)
    assert "pglite_pydb" in out

def test_config_fields_preserved():
    from dataclasses import fields
    from pglite_pydb import PGliteConfig
    names = {f.name for f in fields(PGliteConfig)}
    expected = {
        "timeout", "cleanup_on_exit", "log_level", "socket_path",
        "work_dir", "node_modules_check", "auto_install_deps",
        "extensions", "node_options", "use_tcp", "tcp_host", "tcp_port",
    }
    assert expected.issubset(names)

def test_windows_rejects_explicit_unix_socket():
    import sys, pytest
    if sys.platform != "win32":
        pytest.skip("Windows-only")
    from pglite_pydb import PGliteConfig
    with pytest.raises(RuntimeError, match="Windows"):
        PGliteConfig(use_tcp=False)

def test_legacy_import_fails():
    with pytest.raises(ModuleNotFoundError):
        import py_pglite  # noqa: F401   — hard rename (Q2)
```

**Gate**: all of the above MUST pass on both `ubuntu-latest` and `windows-latest` before the `0.6.0` tag is pushed.
