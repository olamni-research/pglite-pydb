# Implementation Plan: Port `py_pglite` to `pglite-pydb` (Cross-Platform)

**Branch**: `001-pglite-pydb-port` | **Date**: 2026-04-20 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-pglite-pydb-port/spec.md`

## Summary

Rename the distribution `py-pglite` → `pglite-pydb` and the importable package `py_pglite` → `pglite_pydb`, while adding first-class Windows/PowerShell support without regressing Linux/macOS. The technical approach is a 12-step, mechanically-verifiable refactor: (1) baseline and rename the module directory with `git mv` so history is preserved, (2) update every build/tooling/documentation reference to the new name, (3) centralise platform detection into `src/pglite_pydb/_platform.py`, (4) swap Unix-socket defaults for TCP on Windows while leaving Linux/macOS defaults untouched, (5) replace bare `"npm"` / `"node"` subprocess strings with a `shutil.which`-based resolver that understands Windows' `.cmd`/`.exe` suffixes, (6) replace the POSIX-only process-group termination with a `psutil`-based cross-platform walker, (7) introduce a Python task runner as the single source of truth for contributor tasks (with `Makefile` retained as a thin delegator), (8) extend CI to a `{ubuntu-latest, windows-latest} × {Python 3.10–3.14} × {Node 20 LTS, Node 22 LTS}` matrix with per-OS xdist exercising, and (9) verify and release at version `0.6.0`. Every step leaves the Linux suite green.

## Technical Context

**Language/Version**: Python 3.10, 3.11, 3.12, 3.13, 3.14 (library; supports every current release at port time)
**Primary Dependencies**: `psycopg` (PostgreSQL driver), `psutil` (process-tree management), `SQLAlchemy` + `Django` (optional integration targets), `pytest` (test harness + plugin host), Node.js 20 LTS or 22 LTS (runs the PGlite server as a subprocess, not bundled)
**Storage**: In-process PGlite (embedded Postgres-compatible, WASM) invoked via a Node subprocess; no persistent storage by design — this library *is* the storage layer for downstream test suites
**Testing**: `pytest` (existing suite under `tests/`), `pytest-xdist` (parallel worker support, first-class per Q3 clarification), plus `examples/` run as smoke tests in CI
**Target Platform**: Linux x86_64 (primary, existing), macOS (existing, same code path), Windows 10+ / Windows 11 PowerShell (new target)
**Project Type**: Single-project Python library (src-layout) with an optional pytest plugin entry-point and Django/SQLAlchemy integration sub-packages
**Performance Goals**: Fixture cold-start under 5 s on Windows (matching Linux); 100 consecutive fixture lifecycles leave zero orphans (SC-003); `pytest -n 4` on Windows completes with zero port collisions across 10 invocations (SC-003a); CI wall-clock on `windows-latest` within 2× `ubuntu-latest` (SC-004)
**Constraints**: Zero regression on Linux (SC-002 — exact pass-count equality required); no new runtime dependencies beyond what `pyproject.toml` already declares; no Unix-domain-socket transport on Windows (AF_UNIX exists on Win10+ but PGlite's Node server writes socket paths that Windows psycopg cannot address); hard rename, no backward-compat shim (Q2)
**Scale/Scope**: ~12 `.py` files under `src/py_pglite/` moving to `src/pglite_pydb/`; ~25 test files; one Makefile, one README, one CI workflow, one pyproject.toml; a single `pglite_manager.js` helper that is **generated at runtime** inside `manager.py` (not a static file) so no JS source needs renaming

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status**: **N/A — constitution unratified.** `.specify/memory/constitution.md` is the unfilled template with all principles still as `[PRINCIPLE_N_NAME]` placeholders. No ratified principles exist to gate against.

**Implications for this plan**: Because there is no binding constitution, no gate can fail. The plan proceeds on the self-consistency of the spec and clarifications alone. If the team ratifies a constitution later (e.g. via `/speckit.constitution`) before this refactor merges, a re-check pass will be appended to this file.

**Re-evaluation after Phase 1 design**: **N/A** (unchanged — constitution still unratified).

## Project Structure

### Documentation (this feature)

```text
specs/001-pglite-pydb-port/
├── plan.md              # This file (/speckit.plan command output)
├── spec.md              # Feature spec (authored by /speckit.specify + /speckit.clarify)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/
│   └── public-api.md    # Phase 1 output — preservation contract for Python public API, pytest plugin name, config fields, fixture names
├── checklists/
│   └── requirements.md  # Spec-quality checklist (authored by /speckit.specify)
└── tasks.md             # Phase 2 output (/speckit.tasks command — NOT created here)
```

### Source Code (repository root)

```text
# Before the refactor (current state)
src/
└── py_pglite/
    ├── __init__.py
    ├── clients.py
    ├── config.py
    ├── extensions.py
    ├── fixtures.py
    ├── manager.py
    ├── pytest_plugin.py
    ├── utils.py
    ├── django/
    │   ├── __init__.py
    │   ├── backend/
    │   │   ├── __init__.py
    │   │   └── base.py
    │   ├── fixtures.py
    │   └── utils.py
    └── sqlalchemy/
        ├── __init__.py
        ├── fixtures.py
        ├── manager.py
        ├── manager_async.py
        └── utils.py

# After the refactor (target state)
src/
└── pglite_pydb/
    ├── __init__.py
    ├── _platform.py           # NEW — single sys.platform branching utility (Step 6)
    ├── clients.py
    ├── config.py              # UPDATED — __post_init__ auto-selects TCP on Windows (Step 7)
    ├── extensions.py
    ├── fixtures.py
    ├── manager.py             # UPDATED — _resolve_node_bin() + _terminate_process_tree() (Steps 8, 9)
    ├── pytest_plugin.py
    ├── utils.py
    ├── django/                # (unchanged internal structure; only imports refreshed)
    │   ├── __init__.py
    │   ├── backend/{__init__.py, base.py}
    │   ├── fixtures.py
    │   └── utils.py
    └── sqlalchemy/            # (unchanged internal structure; only imports refreshed)
        ├── __init__.py
        ├── fixtures.py
        ├── manager.py
        ├── manager_async.py
        └── utils.py

tests/                         # (~25 test files; whole-word import rename; no structure change)
├── test_*.py
└── README.md

# Repo root additions
tasks.py                       # NEW — Python task runner (Step 10); canonical home of dev/test/lint/clean/… logic
Makefile                       # UPDATED — each target delegates one-line to `uv run python tasks.py <name>`

.github/workflows/ci.yml       # UPDATED — OS matrix {ubuntu-latest, windows-latest}; Python 3.10–3.14; Node 20+22 (Step 11)
pyproject.toml                 # UPDATED — name/entry-point/module-name/mypy/ruff/coverage paths (Step 3)
README.md                      # UPDATED — install/import examples + one deprecation pointer (Step 5)
CONTRIBUTING.md                # UPDATED — Windows quickstart + task-runner docs (Step 5, 10)
.safety-project.ini            # UPDATED — project name (Step 5)
```

**Structure Decision**: Single-project src-layout (no change to top-level shape). The rename is a directory move plus mechanical-text updates; all existing subpackages (`django/`, `django/backend/`, `sqlalchemy/`) retain their internal structure. The only net-new files are `src/pglite_pydb/_platform.py` (platform utility) and `tasks.py` (cross-platform task runner). No web-application or mobile split applies.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified.**

No violations to justify — Constitution Check is N/A (unratified constitution). The 12 refactor steps do not introduce new architectural complexity; they consolidate existing inline behaviour (signal handling, binary resolution, platform checks) into three named helpers and move task logic out of the Makefile. Step-by-step task decomposition is deferred to `tasks.md` (produced by `/speckit.tasks`).

## Phase 0 / Phase 1 Artefacts

Produced alongside this plan, in the same feature directory:

- `research.md` — resolves the one open `NEEDS CLARIFICATION` (the Python-matrix discrepancy between spec and CI), records platform-behaviour research for AF_UNIX-on-Windows, psutil process-tree termination, xdist port allocation, and the `shutil.which` / `.cmd` resolution pattern; also documents the Python task-runner choice (plain `tasks.py` vs `taskipy` vs `invoke`).
- `data-model.md` — entity catalogue (Distribution package, Importable package, Platform profile, Transport configuration, Node binary handle, Managed process tree, Refactor step, Deprecation pointer) with fields, invariants, and the 12 refactor-step records as a tabular data model.
- `quickstart.md` — operator-style guide for running each of the 12 steps: the exact commands, the verification check, and the commit boundary. Usable as a reviewer's runbook.
- `contracts/public-api.md` — the preservation contract: the list of public symbols, fixture names, configuration fields, exception types, and pytest-plugin entry-point name whose shapes MUST NOT change across the rename.

## Next Command

`/speckit.tasks` — converts this plan + its Phase 0/1 artefacts into `tasks.md`, a dependency-ordered checklist corresponding one-to-one with the 12 refactor steps (plus any prerequisite setup tasks the task-generator identifies).
