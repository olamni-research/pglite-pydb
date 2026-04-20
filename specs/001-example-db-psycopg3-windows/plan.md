# Implementation Plan: Windows Sample Database Example with TCP and Named Pipe Transports

**Branch**: `001-example-db-psycopg3-windows` | **Date**: 2026-04-20 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-example-db-psycopg3-windows/spec.md`

## Summary

Deliver a Windows-only runnable example inside the existing the repo root project that loads a vendored copy of the JannikArndt PostgreSQL sample database into a persisted PGlite data directory, installs exactly 10 named stored procedures (per the spec catalog), and lets a developer run the same example over either **TCP** (localhost) or **modern named-pipe** transport with a single CLI flag. A parallel pytest suite exercises both transports end-to-end and is hard-skipped on non-Windows runners. Trust-based auth as a single local role `example_user` applies on both transports; no upstream dump rows are mutated (mutations target example-owned `audit_log` / `country_overlay` tables).

Technical approach: reuse `py_pglite.PGliteManager` (already runs a Node-based PGlite process with a socket bridge) by adding a Windows-specific launcher that spawns a Node bridge bound to **exactly one** transport per process — either a TCP listener on `127.0.0.1:<port>` **or** a Windows named pipe `\\.\pipe\<name>`, never both — while keeping PGlite's on-disk data directory at a deterministic repo-relative path. For a single CLI run, the launcher matches the bridge's transport to `--transport`. The pytest session-scoped fixture spawns two independent bridge processes (one TCP, one pipe) sharing the same data directory (research R11) so matrix tests have a bridge per transport without either listener being bound during the other's run. The Python client is `psycopg 3`, matching the project's existing optional dependency. The 10 procedures are authored as PL/pgSQL scripts installed on first run.

## Technical Context

**Language/Version**: Python ≥3.10 (matches `pglite-pydb` `requires-python`); Node.js ≥18 (carried over from `py-pglite`'s existing PGlite runtime dependency).
**Primary Dependencies**: `py-pglite` (in-tree, from the repo root), `psycopg[binary]>=3.1` (already listed as optional), `pytest>=7`, `pytest-asyncio`, `pywin32` (for Windows named-pipe helpers in the launcher), `@electric-sql/pglite` (Node, existing) plus a small Node shim script that bridges PGlite to a TCP listener and a Windows named pipe.
**Storage**: Persisted PGlite on-disk data directory at `examples/windows_sample_db/data/pgdata/` by default (configurable via `--data-dir`); vendored sample dump at `examples/windows_sample_db/data/sample_db.sql` with `UPSTREAM_LICENSE` and `UPSTREAM_ATTRIBUTION.md` alongside.
**Testing**: `pytest` with the project's existing `pytest11` plugin registration (`py_pglite`), plus a new `@pytest.mark.windows_only` marker gated on `sys.platform == "win32"`. Matrix runs the same test file twice — once per transport — via a parametrized fixture.
**Target Platform**: Windows 10 build 17763+ and Windows 11, 64-bit. Non-Windows platforms skip the entire suite with reason `"requires Windows"`.
**Project Type**: Sub-project example + test suite within the monorepo (the repo root Python package is the host; this feature adds a new `examples/windows_sample_db/` folder and a new `tests/windows_sample_db/` folder — no changes to the public `py_pglite` API).
**Performance Goals**: First run (dump load + procedure install) completes in ≤ 60s on a modern laptop; warm restart (data directory already populated) produces first query output in ≤ 10s (spec SC-002); each stored-procedure call returns in ≤ 500ms p95 against the sample dataset; named-pipe call latency within 30% of TCP on the same procedure (informational, not a gate).
**Constraints**: Windows-only; no TCP listener may be bound when `--transport pipe` is selected (spec FR-008); zero network access on first run (vendored dump, FR-001); single local role `example_user` only (FR-021); upstream `countries`/`airports`/etc. rows remain byte-identical across runs (FR-023).
**Scale/Scope**: One new example entry point, ~10 PL/pgSQL procedures (~200–400 lines SQL total), ~20 pytest test cases (10 happy-path × 2 transports split across parametrization, plus 10 negative-path cases), one vendored `.sql` file of the JannikArndt dump (anticipated few MB).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution at `.specify/memory/constitution.md` contains only template placeholders (unratified — all `[PRINCIPLE_N_NAME]`, `[GOVERNANCE_RULES]`, version `[CONSTITUTION_VERSION]`). No concrete principles are in force, so there are no ratified gates to evaluate.

**Gate result**: PASS (vacuously — no ratified constraints exist). If the constitution is later ratified, this plan must be re-checked against it; `/speckit.constitution` should be run to populate real principles before this feature ships to production.

## Project Structure

### Documentation (this feature)

```text
specs/001-example-db-psycopg3-windows/
├── plan.md              # This file
├── research.md          # Phase 0 output (this run)
├── data-model.md        # Phase 1 output (this run)
├── quickstart.md        # Phase 1 output (this run)
├── contracts/
│   ├── cli.md           # Example CLI contract (args, flags, exit codes)
│   ├── procedures.md    # Signatures, return shapes, and error codes for the 10 procedures
│   └── transport.md     # Transport connection contract (TCP + named-pipe)
├── checklists/
│   └── requirements.md  # Existing from /speckit.specify
├── spec.md              # Existing from /speckit.specify + /speckit.clarify
└── tasks.md             # Phase 2 output (NOT created here — /speckit.tasks)
```

### Source Code (repository root)

```text
/
├── examples/
│   └── windows_sample_db/                     # NEW — this feature's runnable example
│       ├── __init__.py
│       ├── run_example.py                     # CLI entry point (argparse → Transport → run)
│       ├── launcher.py                        # Boots PGlite with TCP + pipe listeners + data dir
│       ├── loader.py                          # Vendored-dump integrity check + one-shot load
│       ├── procedures.py                      # Installs/verifies the 10 stored procedures
│       ├── transport.py                       # Builds psycopg connection strings per transport
│       ├── sql/
│       │   ├── 00_schema_overlay.sql          # Creates audit_log + country_overlay tables
│       │   ├── 01_role.sql                    # Creates trust-auth role example_user
│       │   └── 10_procedures.sql              # All 10 PL/pgSQL procedure definitions
│       ├── node/
│       │   └── pglite_bridge.js               # Node shim: PGlite → TCP + named-pipe bridge
│       ├── data/
│       │   ├── sample_db.sql                  # Vendored JannikArndt dump (checked in)
│       │   ├── sample_db.sql.sha256           # Integrity checksum
│       │   ├── UPSTREAM_LICENSE               # Upstream LICENSE preserved verbatim
│       │   ├── UPSTREAM_ATTRIBUTION.md        # Provenance + commit SHA of upstream
│       │   └── pgdata/                        # PGlite on-disk data dir (gitignored)
│       └── README.md                          # Getting-started matching quickstart.md
└── tests/
    └── windows_sample_db/                     # NEW — this feature's test suite
        ├── __init__.py
        ├── conftest.py                        # Windows-only skip + transport-matrix fixtures
        ├── test_loader.py                     # Dump load + checksum + idempotent reload
        ├── test_procedures_happy.py           # 10 procedures × 2 transports = happy-path matrix
        ├── test_procedures_errors.py          # 10 negative-path cases (one per procedure)
        ├── test_transport_tcp.py              # TCP-specific behavior (port-in-use, etc.)
        ├── test_transport_pipe.py             # Named-pipe specific (unique-pipe flag, collision)
        ├── test_persistence.py                # Data survives process exit (FR-015)
        └── test_cleanup.py                    # Temp data dirs removed (FR-016)
```

**Structure Decision**: Sub-project layout. All new code lives under `examples/windows_sample_db/` and `tests/windows_sample_db/`. No changes to `src/py_pglite/*` — we depend on its existing `PGliteManager` + psycopg client machinery as a library. The Node bridge script (`node/pglite_bridge.js`) is invoked by `launcher.py` via subprocess, mirroring how `py_pglite.manager` already spawns Node. The spec-kit artifacts remain at the outer repo root under `specs/`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

N/A — Constitution unratified (see Constitution Check above). No violations to track.
