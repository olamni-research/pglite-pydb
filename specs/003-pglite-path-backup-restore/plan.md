# Implementation Plan: Mandatory Data Path + Backup/Restore Commands

**Branch**: `003-pglite-path-backup-restore` | **Date**: 2026-04-21 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-pglite-path-backup-restore/spec.md`

## Summary

Three tightly-coupled capabilities land together on top of the existing `pglite-pydb` wrapper:

1. **Mandatory data-directory path** — `PGliteConfig` requires an explicit `data_dir: Path`. The wrapper resolves it through symlinks (`Path.resolve(strict=False)`), creates it if missing, rejects it if occupied by non-PGlite content, acquires a cross-platform advisory file lock on `<data-dir>/.pglite-pydb/instance.lock`, and passes the resolved path as the `dataDir` argument to the PGlite WASM instance inside the generated `pglite_manager.js` (`new PGlite("file://" + dataDir, {...})`). This replaces the current ephemeral-temp-dir default: data lives exactly where the caller says, nowhere else.

2. **Sidecar per-instance config** — `<data-dir>/.pglite-pydb/config.json` persists the configured backup location (and is reserved for future instance-scoped settings). Read/written only through wrapper APIs (`pglite-pydb config set-backup-location`), never hand-edited; excluded from `--full-snapshot` archives; preserved as-is on `restore --full-snapshot`.

3. **`pglite-pydb` CLI with `backup` and `restore` subcommands** — a new console entry point (`pglite-pydb = pglite_pydb.cli:main`, argparse-based, no new deps) drives both logical (`.tar.gz` of one `*.sql` per schema + `manifest.json`) and physical (`FULL_SNAPSHOT_*.tar.gz`) backups against an instance at a mandatory `--data-dir`. Logical backups shell out to **`pg_dump`** (required on `PATH`, documented runtime prereq) through a wrapper-launched PGlite server over TCP — `pg_dump`'s transactional snapshot is what guarantees FR-017 internal consistency, whether the wrapper holds the exclusive FR-006 lock (default) or attaches to a foreign running server (`--force-hot`). Physical full snapshots use Python's stdlib `tarfile` over the frozen data directory while the FR-006 lock is held. Restore mirrors: logical path runs each SQL dump through `psql` (also required on `PATH`); full-snapshot path extracts the archive into the target data directory after the documented two-stage confirmation. Timestamp format is `YYYYMMDD-HHMMSS.fff` (UTC, millisecond precision) so chronological sort == lexical sort, and sub-second uniqueness is guaranteed even under rapid-fire invocation.

Cross-platform parity is preserved via the existing `_platform.py` layer: Windows file locking uses `msvcrt.locking`, POSIX uses `fcntl.flock`; path resolution, archive handling, and subprocess invocation reuse patterns already validated by feature 001.

## Technical Context

**Language/Version**: Python 3.10–3.14 (inherited from feature 001; no version bump)
**Primary Dependencies**: `psutil` (already present, process-tree management), `psycopg` (already optional, used to drive PGlite over TCP for dumps' prerequisite ping), stdlib `tarfile`/`argparse`/`json`/`hashlib`; **new external runtime prereqs**: `pg_dump` and `psql` client binaries on `PATH` (PostgreSQL 15+ client tools; wire-compatible with PGlite). No new Python package dependencies.
**Storage**: Local filesystem only — instance data under a user-supplied `data_dir`; sidecar config at `<data-dir>/.pglite-pydb/config.json`; backup containers at the configured backup location.
**Testing**: `pytest` (existing), `pytest-xdist` (existing); new markers `@pytest.mark.windows_only` / `skipif(not shutil.which("pg_dump"))` for backup suites; integration tests exercise the CLI via `subprocess` against the installed console script.
**Target Platform**: Linux x86_64, macOS, Windows 10+ / Windows 11 PowerShell — identical semantics on all three (FR-007).
**Project Type**: Single-project Python library (src-layout) — no structural change; one new subpackage `src/pglite_pydb/cli/` and one new module `src/pglite_pydb/backup.py`.
**Performance Goals**: `backup` on a 100 MB / 10-schema instance completes within 30 s on Windows; `restore --latest` prompt-to-completion under the same load within 45 s; lock acquisition latency < 100 ms on idle data directory; 10 rapid-fire `backup` invocations (SC-008) complete without collision in < 60 s wall-clock total.
**Constraints**: Zero new Python runtime deps (FR-029 cross-platform parity hinges on stdlib-only code paths); `pg_dump`/`psql` documented as prerequisites (not auto-installed); no change to feature 001's public Python API — the mandatory `data_dir` is additive on `PGliteConfig` but existing callers that relied on the auto-temp-dir default will break deliberately (this is the FR-001 contract); no central registry (per FR-008 — each instance is self-contained).
**Scale/Scope**: ~4 new source files (`cli/__init__.py`, `cli/main.py`, `backup.py`, `_lock.py`), ~3 updated files (`config.py`, `manager.py`, `pyproject.toml`); ~8 new test files under `tests/` covering path semantics, locking, logical backup/restore round-trips, full-snapshot round-trips, `--latest` selection, `--overwrite` confirmations, sidecar config, cross-platform portability; ~36 functional requirements in scope (FR-001..FR-036).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status**: **N/A — constitution unratified.** `.specify/memory/constitution.md` remains the unfilled template (all principles are `[PRINCIPLE_N_NAME]` placeholders). No ratified principles exist to gate against, matching the posture documented in feature 001's plan.

**Implications for this plan**: No gate can fail. The plan proceeds on the self-consistency of the spec, its 2026-04-21 clarifications, and the already-shipped conventions from feature 001.

**Re-evaluation after Phase 1 design**: **N/A** (unchanged — constitution still unratified).

## Project Structure

### Documentation (this feature)

```text
specs/003-pglite-path-backup-restore/
├── plan.md              # This file (/speckit.plan command output)
├── spec.md              # Feature spec (authored by /speckit.specify + /speckit.clarify)
├── research.md          # Phase 0 output — resolves pg_dump/psql prereq, lock mechanism, timestamp format, tar layout
├── data-model.md        # Phase 1 output — entities: Instance, DataDirectory, BackupLocation, Container (logical/full), Manifest, LockFile, SidecarConfig
├── quickstart.md        # Phase 1 output — operator-level walkthrough of the 10 acceptance scenarios end-to-end
├── contracts/
│   ├── cli.md           # Phase 1 output — argparse command surface (backup, restore, config) with exit codes and stderr contracts
│   └── manifest.md      # Phase 1 output — JSON schema for backup manifest.json (logical + full-snapshot variants)
├── checklists/          # (pre-existing — authored by earlier /speckit runs)
└── tasks.md             # Phase 2 output (/speckit.tasks command — NOT created here)
```

### Source Code (repository root)

```text
src/
└── pglite_pydb/
    ├── __init__.py               # (existing; re-exports may add `from .backup import ...` for convenience)
    ├── _platform.py              # (existing; unchanged)
    ├── _lock.py                  # NEW — cross-platform advisory file lock (fcntl on POSIX, msvcrt on Windows)
    ├── clients.py                # (existing; unchanged)
    ├── config.py                 # UPDATED — `data_dir: Path` becomes mandatory (no default), add resolve-and-validate in __post_init__
    ├── extensions.py             # (existing; unchanged)
    ├── fixtures.py               # (existing; fixtures that instantiate PGliteConfig grow a mandatory tmp_path-based data_dir arg)
    ├── manager.py                # UPDATED — _setup_work_dir becomes _prepare_data_dir; acquire _lock.InstanceLock;
    │                             #           embed dataDir into generated pglite_manager.js; write sidecar on first start
    ├── backup.py                 # NEW — BackupEngine class: create_logical(), create_full_snapshot(), restore_logical(),
    │                             #       restore_full_snapshot(), list_containers(), select_latest(kind=...)
    ├── pytest_plugin.py          # UPDATED — plugin-managed fixtures pass a tmp_path subdir as data_dir
    ├── utils.py                  # (existing; small additions for ts formatting if not imported from stdlib)
    ├── cli/                      # NEW package
    │   ├── __init__.py           # NEW — exposes `main` for console_scripts
    │   └── main.py               # NEW — argparse entry point: `pglite-pydb <backup|restore|config> ...`
    ├── django/                   # (existing; Django fixtures updated to demand data_dir like core fixtures)
    │   └── ...
    └── sqlalchemy/               # (existing; same fixture update)
        └── ...

tests/
├── test_data_dir_mandatory.py        # NEW — FR-001..FR-007, SC-001, SC-002, SC-003
├── test_instance_lock.py             # NEW — FR-006 (concurrent start fail-fast), Linux + Windows
├── test_sidecar_config.py            # NEW — FR-008..FR-011 (persist, read, change via CLI)
├── test_backup_logical.py            # NEW — FR-012..FR-018, SC-004, SC-008 (rapid-fire 10x uniqueness)
├── test_backup_full_snapshot.py      # NEW — FR-031..FR-033 (physical snapshot + sidecar exclusion)
├── test_restore_logical.py           # NEW — FR-019..FR-028 (named, --latest, --overwrite, conflict, corrupt, mandatory path)
├── test_restore_full_snapshot.py     # NEW — FR-034..FR-036 (two-stage confirm, lock, sidecar preservation)
├── test_cross_platform_portability.py# NEW — SC-005 (Linux-produced artifact restored on Windows in CI matrix + vice versa)
└── (existing tests continue to pass unchanged except fixture call sites that now pass data_dir)

pyproject.toml                          # UPDATED — add `[project.scripts] pglite-pydb = "pglite_pydb.cli.main:main"`;
                                        #           version bump to 2026.4.21.2 (SameDay build+1) when implementation ships
CLAUDE.md                               # UPDATED (by this command) — SPECKIT block now points at feature 003 artifacts
```

**Structure Decision**: Same single-project src-layout established by feature 001. New code is additive — one new subpackage (`cli/`) and two new modules (`backup.py`, `_lock.py`); no existing file is moved or renamed. The CLI lives under `pglite_pydb.cli.main` (not at package root) so it can grow further subcommands without polluting the import namespace.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified.**

No violations to justify — Constitution Check is N/A (unratified constitution). One judgment call worth recording:

| Decision | Why | Simpler alternative rejected because |
|----------|-----|--------------------------------------|
| Shell out to `pg_dump` / `psql` instead of emitting SQL through `psycopg` introspection | `pg_dump` is the canonical transactional-snapshot mechanism for PostgreSQL wire-protocol servers; it gives FR-017 internal consistency for free and handles every type, extension, trigger, default, and sequence correctly. | Hand-rolled introspection (`psycopg` + `COPY TO`) would re-implement `pg_dump`, would not ship with transactional-snapshot guarantees, and would be a long-term maintenance tax for zero benefit over a documented PATH prerequisite. |

## Phase 0 / Phase 1 Artefacts

Produced alongside this plan, in the same feature directory:

- `research.md` — resolves: (R1) `pg_dump` / `psql` as a runtime prerequisite vs. Python-side re-implementation, (R2) cross-platform advisory file-lock mechanism (`fcntl.flock` vs `msvcrt.locking`), (R3) timestamp format (`YYYYMMDD-HHMMSS.fff` UTC) and sub-second uniqueness strategy, (R4) tar layout for logical vs full-snapshot containers, (R5) sidecar config location trade-off (inside data directory vs user-home registry — the clarification already selects inside-data-dir; research records *why*), (R6) interactive confirmation UX across TTY detection and `--assume-yes` semantics shared across FR-021/FR-022/FR-025/FR-035, (R7) how "completely empty" is evaluated for FR-035's second confirmation.
- `data-model.md` — entity catalogue: `PGliteInstance` (resolved data_dir as PK), `DataDirectory` (filesystem facts, emptiness predicate), `SidecarConfig` (`config.json` schema), `InstanceLock` (file handle + platform mechanism), `BackupLocation` (resolved absolute path, writable predicate), `BackupContainer` (abstract — `LogicalContainer` and `FullSnapshotContainer` variants, each with naming grammar and validity invariants), `Manifest` (logical vs full-snapshot JSON shapes), `SchemaSelection` (`single | list | all`), plus the state diagrams for `backup` and `restore` (including the two-stage confirmation for `restore --full-snapshot`).
- `contracts/cli.md` — the canonical CLI surface: argument/option grammar for `pglite-pydb backup`, `pglite-pydb restore`, and `pglite-pydb config set-backup-location | get-backup-location`; exit-code table; stderr message catalogue mapped one-to-one against every FR failure mode named in the spec; TTY vs non-TTY behaviour of `--assume-yes`.
- `contracts/manifest.md` — JSON schema (informal, stable field list) for `manifest.json` in both container kinds: `schema_version`, `kind` (`logical` | `full-snapshot`), `created_at` (ISO-8601 UTC), `source_data_dir` (resolved absolute path), `included_schemas` (logical only — array of strings; `["*"]` sentinel for "all" mode), `pglite_pydb_version`, `postgres_server_version` (from `SELECT version();` at dump time), `container_filename`.
- `quickstart.md` — operator runbook: install prerequisites (Node 20/22, `pg_dump`/`psql`, `pglite-pydb`), bootstrap an instance at an explicit path, configure its backup location, exercise each of the three logical selection modes, take a full snapshot, restore by name and by `--latest` with confirmations, reproduce the FR-035 two-stage confirmation flow, verify sidecar preservation across full-snapshot restore.

`CLAUDE.md` will be updated at the end of this command to point its `<!-- SPECKIT -->` block at the feature 003 artifacts (replacing the feature 001 pointers).

## Next Command

`/speckit.tasks` — converts this plan + its Phase 0/1 artefacts into `tasks.md`, a dependency-ordered checklist grouped by user story (US1 mandatory path → US2 backup → US3 restore, then full-snapshot mode as a parallel track layered on top of US2/US3).
