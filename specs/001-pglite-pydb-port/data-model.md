# Phase 1 Data Model: pglite-pydb Port

**Feature**: 001-pglite-pydb-port · **Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

This document enumerates the conceptual entities the refactor reasons about. Because the feature is a rename + cross-platform enablement (not a product feature with persistent domain state), "data model" here captures **configuration objects, platform-decision records, and the refactor-step ledger** — not database tables.

## Entity 1 — Distribution package

The published wheel/sdist artefact on PyPI.

| Field              | Type    | Constraint / Invariant                                             |
|--------------------|---------|--------------------------------------------------------------------|
| `name`             | string  | MUST equal `pglite-pydb` after refactor; MUST have been `py-pglite` before |
| `version`          | string  | MUST be `0.6.0` or higher on the first post-refactor release (FR-016) |
| `import_name`      | string  | MUST equal `pglite_pydb` (maps `name` ↔ importable package)        |
| `pytest_plugin`    | string  | Entry-point name MUST equal `pglite_pydb` (FR-009)                  |
| `python_requires`  | string  | Unchanged: `>=3.10`                                                 |
| `node_engines`     | string  | Declared: `>=20` (added, informational)                             |

**Relationships**: one Distribution package → produces one Importable package (1:1).

## Entity 2 — Importable package

The Python package directory under `src/`.

| Field              | Type       | Constraint / Invariant                                         |
|--------------------|------------|----------------------------------------------------------------|
| `directory`        | Path       | `src/pglite_pydb/` after refactor (was `src/py_pglite/`)       |
| `public_symbols`   | list[str]  | See [contracts/public-api.md](./contracts/public-api.md) — MUST be preserved by name and signature |
| `subpackages`      | list[str]  | `django`, `django.backend`, `sqlalchemy` — MUST retain internal structure |
| `platform_utility` | Path       | `src/pglite_pydb/_platform.py` — NEW; centralises `sys.platform` branches (FR-010) |

**Relationships**: one Importable package → exposes many Public symbols; one Importable package → imports one Platform profile.

## Entity 3 — Platform profile

A lightweight, process-wide constant record of the host OS, owned by `src/pglite_pydb/_platform.py`.

| Field                    | Type | Constraint / Invariant                              |
|--------------------------|------|-----------------------------------------------------|
| `IS_WINDOWS`             | bool | `sys.platform == "win32"`                           |
| `IS_LINUX`               | bool | `sys.platform.startswith("linux")`                  |
| `IS_MACOS`               | bool | `sys.platform == "darwin"`                          |
| `SUPPORTS_UNIX_SOCKETS`  | bool | `not IS_WINDOWS` (see R2 in research.md)            |

**Invariants**:
- Exactly one of `IS_WINDOWS`, `IS_LINUX`, `IS_MACOS` is `True` on every supported host.
- All `sys.platform` checks elsewhere in `pglite_pydb` MUST read from this module (FR-010).

**State transitions**: none (immutable for the process lifetime).

## Entity 4 — Transport configuration

The resolved connection descriptor emitted by `PGliteConfig.__post_init__` after applying platform-aware defaults to user-supplied values.

| Field              | Type         | Constraint / Invariant                                                    |
|--------------------|--------------|---------------------------------------------------------------------------|
| `mode`             | enum         | `"unix"` or `"tcp"`                                                        |
| `socket_path`      | Path / None  | set iff `mode == "unix"`; MUST be None on Windows                          |
| `tcp_host`         | str / None   | set iff `mode == "tcp"`; defaults to `"127.0.0.1"` (loopback only, per FR-004) |
| `tcp_port`         | int / None   | set iff `mode == "tcp"`; `0` means "OS-assigned ephemeral" (see R4)        |

**State transitions** (applied inside `__post_init__`, one pass):

```
(user input)
     │
     ▼
user set use_tcp=False?
     │
     ├─ YES ─▶ IS_WINDOWS? ──── YES ──▶ raise RuntimeError (FR-005)
     │                      └─ NO  ──▶ mode = "unix"
     │
     └─ NO ──▶ user set use_tcp=True? ─── YES ──▶ mode = "tcp"
                                       └─ NO ──▶ IS_WINDOWS? ── YES ──▶ mode = "tcp" (log info)
                                                              └─ NO  ──▶ mode = "unix" (default preserved)
```

**Relationships**: one `PGliteConfig` input → one Transport configuration output; one `PGliteManager` consumes one Transport configuration.

## Entity 5 — Node binary handle

The resolved absolute path of `node` or `npm`, returned by `_resolve_node_bin(name)`.

| Field             | Type | Constraint / Invariant                                                  |
|-------------------|------|-------------------------------------------------------------------------|
| `requested_name`  | str  | `"node"` or `"npm"`                                                     |
| `resolved_path`   | str  | absolute path; `shutil.which()` result against OS-specific candidate list |
| `matched_suffix`  | str  | `""` on POSIX; one of `{"", ".cmd", ".exe"}` on Windows                 |

**Invariants**:
- `resolved_path` MUST be absolute and MUST point to an existing executable.
- On failure, `FileNotFoundError` is raised naming every candidate attempted (FR-006).

**Relationships**: each `PGliteManager.start()` call resolves two Node binary handles (`node`, `npm`).

## Entity 6 — Managed process tree

The PGlite Node subprocess plus every descendant it spawned.

| Field                 | Type        | Constraint / Invariant                                  |
|-----------------------|-------------|---------------------------------------------------------|
| `root_pid`            | int         | PID of the `subprocess.Popen(...)` child returned by `PGliteManager.start()` |
| `identification_mode` | enum        | `"pgid"` on Linux/macOS, `"pid_tree"` on Windows         |
| `descendants`         | list[int]   | dynamic; obtained via `psutil` on Windows, implicit via PGID on POSIX |
| `termination_path`    | enum        | `"killpg"` (POSIX) / `"psutil_tree"` (Windows)          |

**State transitions**:

```
(started)
   │   proc = subprocess.Popen(..., preexec_fn=os.setsid if POSIX else None)
   ▼
(running)
   │   termination requested
   ▼
(terminating-graceful)
   │   POSIX: killpg(pgid, SIGTERM)
   │   Windows: psutil.Process(root_pid).children(recursive=True) → terminate()
   │   wait up to 5 s
   ├─ exited cleanly ──▶ (stopped)
   │
   └─ timeout ──▶ (terminating-forceful)
                      │   POSIX: killpg(pgid, SIGKILL)
                      │   Windows: psutil .kill() any is_running() survivors
                      ▼
                   (stopped)
```

**Relationships**: one `PGliteManager` owns one Managed process tree (lifetime = fixture lifetime).

## Entity 7 — Refactor step

An atomic, independently-committable unit of work in the 12-step plan. Modelled here as a ledger for `tasks.md` generation.

| Field                | Type                           |
|----------------------|--------------------------------|
| `step_id`            | int (1–12)                     |
| `title`              | str                            |
| `scope`              | str — what files/areas it touches |
| `verification`       | str — the command that proves success |
| `commit_boundary`    | bool — must the step end in a green-test commit? |
| `depends_on`         | list[int]                      |

### Tabular listing

| Step | Title                             | Scope                               | Verification                                             | Commit? | Depends on |
|------|-----------------------------------|-------------------------------------|----------------------------------------------------------|---------|------------|
| 1    | Baseline & lockfiles              | branch, uv.lock, package-lock.json  | `uv run pytest tests/ -x --tb=short` (record count)       | YES     | —          |
| 2    | `git mv src/py_pglite src/pglite_pydb` | directory rename only            | `git status` shows only renames, no content changes       | YES     | 1          |
| 3    | `pyproject.toml` single-file pivot | 7 lines in `pyproject.toml`         | `uv sync --reinstall` succeeds                           | YES     | 2          |
| 4    | Mass import rename                 | src/, tests/, examples/, conftest.py | `rg -w "py_pglite"` in `.py` files returns 0; Linux tests green | YES     | 3          |
| 5    | Docs/CI/Makefile text refs         | README, CONTRIBUTING, ci.yml, .safety-project.ini | Linux CI passes                                  | YES     | 4          |
| 6    | `_platform.py` utility             | NEW `src/pglite_pydb/_platform.py`  | `from pglite_pydb._platform import IS_WINDOWS`           | YES     | 4          |
| 7    | Windows TCP auto-select in config  | `src/pglite_pydb/config.py` `__post_init__` | Linux tests green (defaults unchanged); Windows manual spike | YES     | 6          |
| 8    | `_resolve_node_bin` helper         | `src/pglite_pydb/manager.py`        | Linux tests green; Windows manual spike passes            | YES     | 6          |
| 9    | `_terminate_process_tree` helper   | `src/pglite_pydb/manager.py`        | Linux tests green (POSIX path unchanged); 100-cycle fixture test passes on Windows | YES | 6 |
| 10   | Python task runner + Makefile delegation | NEW `tasks.py`, updated `Makefile` | `uv run python tasks.py test` = `make test` on Linux; both work on Windows (PowerShell) | YES | 5 |
| 11   | CI matrix `{ubuntu, windows} × Py3.10-3.14 × Node20/22` + xdist | `.github/workflows/ci.yml` | PR green on both OSes; xdist cell green              | YES     | 7, 8, 9, 10 |
| 12   | Release verification & version bump | `pyproject.toml` version → 0.6.0; `uv build` | `pip install dist/pglite_pydb-*.whl[all]` + smoke test on both OSes; `rg -w "py_pglite\|py-pglite"` returns ≤1 hit (the README deprecation note) | YES | 11 |

## Entity 8 — Deprecation pointer

A single paragraph in `README.md` documenting the rename.

| Field         | Type  | Constraint / Invariant                                                     |
|---------------|-------|----------------------------------------------------------------------------|
| `location`    | Path  | `README.md`, immediately after the project description                    |
| `from_name`   | str   | `py-pglite` (PyPI) / `py_pglite` (import)                                  |
| `to_name`     | str   | `pglite-pydb` (PyPI) / `pglite_pydb` (import)                              |
| `shim_policy` | str   | `"none"` — explicitly states that no backward-compatibility alias is provided |

**Invariants**:
- Exactly one Deprecation pointer exists in the tree (SC-005).
- A whole-word grep for `py_pglite` or `py-pglite` in tracked files returns at most one hit, and that hit is inside the Deprecation pointer.
