---
description: "Task list for feature 001-example-db-psycopg3-windows"
---

# Tasks: Windows Sample Database Example with TCP and Named Pipe Transports

**Input**: Design documents from `specs/001-example-db-psycopg3-windows/`
**Prerequisites**: plan.md, spec.md (US1–US3), research.md (R1–R10), data-model.md, contracts/{cli,procedures,transport}.md, quickstart.md

**Tests**: Included. The feature spec makes the Windows-only automated test suite **User Story 3 (P2)**, so test tasks are first-class here rather than optional.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: `[US1]`, `[US2]`, `[US3]` — omitted for Setup / Foundational / Polish phases
- File paths are project-relative to the repo root `D:\bstdev\pglite\`.

## Path Conventions

Inside the the repo root sub-project (the existing Python package hosting this example):

- Example code: `examples/windows_sample_db/`
- Tests: `tests/windows_sample_db/`

Spec-kit artifacts live at the outer repo root under `specs/001-example-db-psycopg3-windows/` and are not modified by implementation tasks.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directories, wire dependencies, prepare the vendored dataset.

- [X] T001 Create feature directory skeleton (empty `__init__.py` files) at `examples/windows_sample_db/{node,sql,data}/__init__.py` markers and `tests/windows_sample_db/__init__.py`
- [ ] T002 Add `pywin32>=306` to a new `windows-sample-db` optional-deps group in `pyproject.toml` (alongside `psycopg[binary]>=3.1`)
- [X] T003 [P] Register `windows_sample_db` pytest marker under `[tool.pytest.ini_options].markers` in `pyproject.toml`
- [X] T004 [P] Add `examples/windows_sample_db/data/pgdata/` to `.gitignore` so PGlite runtime data is never committed
- [ ] T005 Vendor the upstream PostgreSQLSampleDatabase dump: save the full SQL text dump at `examples/windows_sample_db/data/sample_db.sql`, compute its SHA-256 and write it to `examples/windows_sample_db/data/sample_db.sql.sha256`, and place the upstream LICENSE + a short attribution file at `examples/windows_sample_db/data/UPSTREAM_LICENSE` and `examples/windows_sample_db/data/UPSTREAM_ATTRIBUTION.md` (pin commit SHA)
- [ ] T006 [P] Ensure `@electric-sql/pglite` is declared in `package.json` (add if missing) so the Node bridge has the library available

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared code and SQL that every user story needs. Bridge here supports **TCP only** — the pipe listener is added in US2.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T007 [P] Implement `TransportConfig` + factory with TCP defaults (`kind`, `host`, `port`, `data_dir`) — pipe fields present but unused — in `examples/windows_sample_db/transport.py`
- [X] T008 [P] Implement `LoaderState`, SHA-256 verification of the vendored dump, and three-way status detection (`fresh` = empty dir; `warm` = `PG_VERSION` present; `inconsistent` = any other state, e.g. `postmaster.opts` present but no `PG_VERSION`). On `inconsistent`, `load()` refuses to start and raises a typed error the CLI maps to exit code 6 with a message suggesting `--reset` (spec Edge Case "partially populated data directory"). File: `examples/windows_sample_db/loader.py`
- [X] T009 [P] Author overlay schema (`audit_log`, `country_overlay`) exactly per data-model.md Layer 2 in `examples/windows_sample_db/sql/00_schema_overlay.sql`
- [X] T010 [P] Author role creation + grants for `example_user` (SELECT on upstream, SELECT/INSERT/UPDATE/DELETE on overlay tables) in `examples/windows_sample_db/sql/01_role.sql`
- [X] T011 [P] Author all 10 PL/pgSQL procedures exactly per `specs/001-example-db-psycopg3-windows/contracts/procedures.md` (signatures, SQLSTATE codes `P0002`/`22023`, no raw `RAISE` without `ERRCODE`) in `examples/windows_sample_db/sql/10_procedures.sql`
- [ ] T012 [P] Implement `install()` + `verify()` for the 10 procedures (idempotent via install-marker file, `EXECUTE` grants to `example_user`) in `examples/windows_sample_db/procedures.py`
- [ ] T013 Implement Node bridge with PGlite `dataDir` persistence. Bridge accepts a required `--transport {tcp|pipe}` CLI argument and binds **exactly one** listener per process (never both). For this foundational task, implement the `--transport tcp` path only (`net.createServer().listen(port, '127.0.0.1')`); the `pipe` branch is fleshed out in T027. Include the bridge-layer role gate that rejects any startup packet with `user != example_user` (FR-021) and the `[bridge] start …` + `[bridge] accept …` log lines from `contracts/transport.md`. File: `examples/windows_sample_db/node/pglite_bridge.js`
- [ ] T014 Implement `launcher.py` that spawns `pglite_bridge.js` via subprocess, polls the TCP port for readiness up to 10s, emits `[bridge] accept ...` log parsing, and cleans up the Node process on exit (depends on T013) in `examples/windows_sample_db/launcher.py`
- [ ] T015 Implement `run_example.py` argparse skeleton: all flags from `contracts/cli.md`, non-Windows exit 2 guard, `--reset` handling, and logging format exactly per the CLI contract (depends on T007, T014) in `examples/windows_sample_db/run_example.py`

**Checkpoint**: Foundation ready — US1 implementation can now begin.

---

## Phase 3: User Story 1 — Run the sample database example over TCP on Windows (P1) 🎯 MVP

**Goal**: A first-time developer on Windows clones, runs one command, sees the dump loaded and all 10 procedures invoked over TCP. Data persists on disk between runs.

**Independent Test**: From a fresh clone: `python -m examples.windows_sample_db.run_example` (no flags) prints the 10 procedure log lines, exit code 0, `pgdata/` populated; a second invocation reuses `pgdata/` (log says `status=warm`) and finishes in < 10s.

### Tests for User Story 1

> Write these tests FIRST, ensure they FAIL before implementation of T021–T024.

- [ ] T016 [P] [US1] Test loader: fresh run loads dump, SHA-256 mismatch aborts with exit code 3, warm run reuses `pgdata/` (spec FR-001, FR-002, FR-015) in `tests/windows_sample_db/test_loader.py`
- [ ] T017 [P] [US1] Test TCP transport happy path: connect as `example_user`, run `SELECT 1`, confirm role restriction (connecting as `postgres` is rejected by the bridge per FR-021) in `tests/windows_sample_db/test_transport_tcp.py`
- [ ] T018 [P] [US1] Test on-disk persistence: start + quit + restart + query returns same rows; assert `pgdata/PG_VERSION` present after first run (FR-015) in `tests/windows_sample_db/test_persistence.py`
- [ ] T018b [P] [US1] Timing assertion for SC-002: record wall-clock from process start to first procedure result on a warm run; assert `< 10.0 s`. Exclude the preceding fresh-load run from the timed window. File: `tests/windows_sample_db/test_persistence.py`
- [ ] T019 [P] [US1] Test happy-path procedure invocations over TCP only (2 representative procedures: `get_country_by_iso` and `count_airports_per_country`) to prove end-to-end wiring — full 10×2 matrix comes in US3 in `tests/windows_sample_db/test_procedures_happy.py`

### Implementation for User Story 1

- [ ] T020 [US1] Build TCP psycopg 3 connection string in `TransportConfig.to_dsn()` (host, port, user=`example_user`, dbname=`postgres`, no password) in `examples/windows_sample_db/transport.py`
- [ ] T021 [US1] Implement the `invoke_all_procedures()` driver in `run_example.py`: calls each of the 10 procedures with representative inputs (`'DE'`, `('US', 10, 1)`, `()`, `(5)`, `'FR'`, `'CH'`, `'JP'`, `'US'`, `('US', 'United States')`, `'DE'`) and logs `proc=… rows=… elapsed_ms=…` per CLI contract in `examples/windows_sample_db/run_example.py`
- [ ] T022 [US1] Emit the full ordered log contract from `contracts/cli.md` (transport line, dump line, pgdata status, procedures installed, connected line, per-proc lines, done line) in `examples/windows_sample_db/run_example.py`
- [ ] T023 [US1] TCP port-in-use detection in the launcher readiness loop: distinguish "port busy" (exit 5, names the port) from "not yet up" (keep polling) in `examples/windows_sample_db/launcher.py`
- [X] T024 [US1] Write the example README (mirror `quickstart.md` with paths relative to the repo root; link to the spec-kit quickstart at `../../../specs/001-example-db-psycopg3-windows/quickstart.md`) in `examples/windows_sample_db/README.md`

**Checkpoint**: User Story 1 is fully functional — TCP run clone-to-output works, data persists, first-time vs. warm run behaviors are observable. Shippable as MVP.

---

## Phase 4: User Story 2 — Run the same example over a modern named-pipe transport (P2)

**Goal**: `--transport pipe` works end-to-end on Windows: no TCP listener is bound for that run, all 10 procedures return identical results over the pipe, `--unique-pipe` gives per-process names, stable-name collisions fail with a clear error and `--unique-pipe` suggestion.

**Independent Test**: With US1 done, `python -m examples.windows_sample_db.run_example --transport pipe` prints `transport=pipe` log line, `Get-NetTCPConnection -LocalPort 54320` finds nothing during the run, all 10 procedures output; starting a second concurrent pipe run with the default name fails with exit 5 and message suggesting `--unique-pipe`.

### Tests for User Story 2

- [ ] T025 [P] [US2] Test named-pipe happy path: connect over `\\.\pipe\pglite_example`, assert `SELECT 1` works and the bridge accept log records `transport=pipe`; assert no process bound 54320 during the run (via a helper that calls `Get-NetTCPConnection` or `netstat`) in `tests/windows_sample_db/test_transport_pipe.py`
- [ ] T026 [US2] Test `--unique-pipe` and stable-name collision: two concurrent runs with default name → second exits 5 naming the pipe; same two with `--unique-pipe` → both succeed on distinct pipe names (FR-025, FR-026) in `tests/windows_sample_db/test_transport_pipe.py`

### Implementation for User Story 2

- [ ] T027 [US2] Implement the bridge's `--transport pipe` branch: `net.createServer().listen('\\\\.\\pipe\\<name>')` with a securable ACL (creator + Administrators only). The bridge process must not bind any TCP port on this branch (verified by T025). Emit `[bridge] start transport=pipe listen=\\\\.\\pipe\\<name> data_dir=<abs>`. File: `examples/windows_sample_db/node/pglite_bridge.js`
- [ ] T028 [US2] Implement the pywin32 named-pipe → socket-compatible shim and pass the open handle into psycopg via its pre-connected-stream factory (per research R2) in `examples/windows_sample_db/transport.py`
- [ ] T029 [US2] Implement the in-process TCP relay fallback (used only when psycopg's stream-wrap API is unavailable at runtime): bind an ephemeral loopback port, proxy bytes between it and the pipe, tear down on connection close in `examples/windows_sample_db/transport.py`
- [ ] T030 [US2] Wire `--transport pipe`, `--pipe-name`, and `--unique-pipe` handling in argparse and into `TransportConfig`; derive unique name as `pglite_example_<pid>_<uuid4[:8]>` when flag set (FR-025, research R9). Add env-var fallback `PGLITE_EXAMPLE_TRANSPORT` consulted when `--transport` is not given on the command line (FR-009 OR-branch). File: `examples/windows_sample_db/run_example.py`
- [ ] T031 [US2] Pipe-collision detection in the launcher (`WaitNamedPipeW` + bridge stderr inspection for `EADDRINUSE`): exit 5 with message naming the pipe and suggesting `--unique-pipe` (FR-026). File: `examples/windows_sample_db/launcher.py`
- [ ] T031b [US2] Pipe-unavailable detection (denial by policy / AV / insufficient privileges / legacy OS): recognize `CreateNamedPipeW` failure codes **other than** `ERROR_PIPE_BUSY` / `EADDRINUSE` (e.g., `ERROR_ACCESS_DENIED`, `ERROR_PATH_NOT_FOUND`); exit 4 with a message naming the transport requested, the OS error, and recommending `--transport tcp` (FR-010, spec US2 AS-3). File: `examples/windows_sample_db/launcher.py`
- [ ] T032 [US2] Extend `run_example.py` so that when `--transport pipe` is set it skips the TCP dial path entirely (no fallback dial to 54320) — the log's `connected …` line must say `transport=pipe`; and `invoke_all_procedures()` runs over the pipe connection in `examples/windows_sample_db/run_example.py`

**Checkpoint**: US1 and US2 both work. `--transport` toggle exercises both paths with identical procedure output.

---

## Phase 5: User Story 3 — Windows-only automated test suite (P2)

**Goal**: Full pytest suite that runs every procedure over both transports, covers negative paths, verifies upstream immutability, cleans up temp artifacts, and is hard-skipped with a clear reason on non-Windows.

**Independent Test**: `pytest tests/windows_sample_db -v` on Windows: all tests pass, output shows each happy-path test parametrized `[tcp]` and `[pipe]`. Same command on Linux/macOS: exit 0, zero failures, skip reason `"requires Windows"` for every collected module.

### Tests for User Story 3

- [ ] T033 [P] [US3] `conftest.py`: Windows-only gate (`collect_ignore_glob` + module-level `pytestmark`); session-scoped fixture that boots **two** bridge processes sharing the same `dataDir` — `bridge_tcp` (`--transport tcp`) and `bridge_pipe` (`--transport pipe --unique-pipe`) per research R11; a parametrized function-scoped `transport_conn` fixture with `params=["tcp","pipe"]` returns a psycopg 3 connection to whichever bridge matches the current parameter. File: `tests/windows_sample_db/conftest.py`
- [ ] T034 [P] [US3] `_expected.py`: spot-check values drawn from the upstream README (country count, Germany's subdivisions count, a non-empty French neighbor set) for use by happy-path assertions (research R10) in `tests/windows_sample_db/_expected.py`
- [ ] T035 [US3] Replace the US1-only happy-path file with the full 10-procedure × 2-transport matrix, consuming the `transport_conn` fixture; each procedure asserts both a row-shape expectation and a spot-check from `_expected.py` (SC-003, FR-013) — depends on T033, T019, T025 — in `tests/windows_sample_db/test_procedures_happy.py`
- [ ] T036 [P] [US3] 10 negative-path cases, one per procedure, parametrized by transport: `get_country_by_iso('ZZ')` → `P0002`; `list_airports_in_country('US', 0, 1)` → `22023`; `top_countries_by_population(-1)` → `22023`; `list_neighbors('ZZ')` → `P0002`; `languages_spoken_in('ZZ')` → `P0002`; `country_profile_report('ZZ')` → `P0002`; `bulk_log_airports_visited('ZZ')` → `P0002` and `audit_log` unchanged; `rename_country_common_name('ZZ', 'x')` → `P0002`; `rename_country_common_name('US', '')` → `22023`; `assert_country_exists('ZZ')` → `P0002`; `count_airports_per_country` baseline run after all errors proves state unchanged (FR-017, SC-005) in `tests/windows_sample_db/test_procedures_errors.py`
- [ ] T037 [P] [US3] Cleanup test: spawn a run with an ephemeral `--data-dir` under `tempfile.TemporaryDirectory()`, assert directory disappears after context exit; assert no orphan `\\.\pipe\pglite_example*` pipes remain (FR-016, SC-007) in `tests/windows_sample_db/test_cleanup.py`
- [ ] T038 [US3] Upstream-immutability assertion: before the suite runs, record MD5 of `country`, `airport`, `language` upstream tables; after the suite finishes, re-read and compare (FR-023) — add as a session-scoped autouse finalizer in `tests/windows_sample_db/conftest.py`

**Checkpoint**: Full test suite passes on Windows, skips cleanly elsewhere. Feature is complete and verifiable end-to-end.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T039 [P] Add a `regen-sample-sha` target to `Makefile` that recomputes `sample_db.sql.sha256` — run only when intentionally re-vendoring
- [ ] T040 [P] Link the new example from `README.md` under "Examples" with a one-paragraph summary and pointer to `examples/windows_sample_db/README.md`
- [ ] T041 Walk through `specs/001-example-db-psycopg3-windows/quickstart.md` on a clean Windows 11 VM, timing each step; fix any step that exceeds the spec's SC-001 10-minute target (manual validation task)
- [ ] T042 [P] Audit every log line in the example against `contracts/cli.md` and every failure message against FR-010/FR-026 wording requirements; fix any drift in `examples/windows_sample_db/run_example.py` and `examples/windows_sample_db/launcher.py`
- [ ] T043 [P] Run `ruff check` and `mypy` against the new `examples/windows_sample_db/` tree and the new `tests/windows_sample_db/` tree; fix diagnostics
- [ ] T044 CI: add a Windows GitHub Actions matrix entry that runs `pytest tests/windows_sample_db` (or extend the existing workflow); add a Linux entry that runs the same command and asserts exit 0 with all-skipped output

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — T001–T006 can start immediately (T003, T004, T006 parallel-safe with T001/T002; T005 is I/O-heavy — run solo).
- **Foundational (Phase 2)**: Depends on Setup. Within Phase 2: T007/T008/T009/T010/T011/T012 are all `[P]` (distinct files); T013 depends on T006 (Node deps); T014 depends on T013; T015 depends on T007 + T014.
- **User Story 1 (Phase 3)**: Depends on Phase 2 complete. Tests T016–T019 are `[P]` before implementation T020–T024. T020 → T021 → T022 (same file but sequential). T023 is in launcher.py (independent file). T024 is docs-only.
- **User Story 2 (Phase 4)**: Depends on Phase 2 complete (plus T015 and T020 from Phase 3 for the argparse + DSN plumbing). Tests T025/T026 are in the same file — T026 runs after T025.
- **User Story 3 (Phase 5)**: Depends on Phase 2 complete and on the test files created in US1/US2 existing (for T035 rewrite). Can otherwise run parallel to US2 if a second developer is available, but T035 must wait for T033, T019, T025.
- **Polish (Phase 6)**: Depends on all user stories complete.

### User Story Dependencies

- **US1 (P1 MVP)**: Only depends on Foundational. Fully shippable on its own — no pipe support required to demonstrate it.
- **US2 (P2)**: Depends on Foundational. Shares the bridge file (`pglite_bridge.js`) and `run_example.py` with US1 — any US2 task touching those files runs after the corresponding US1 task that created them.
- **US3 (P2)**: Depends on Foundational. The full transport matrix (T035) depends on both US1 and US2 tests existing — if US2 is not yet complete, T035 can still ship a TCP-only matrix and be extended when US2 lands.

### Parallel Opportunities

- **Phase 1**: T003, T004, T006 in parallel with T001.
- **Phase 2**: {T007, T008, T009, T010, T011, T012} all six in parallel; T013 sequential (touches Node bridge); T014 sequential after T013; T015 sequential after T007 + T014.
- **Phase 3 tests**: T016, T017, T018, T019 all parallel (distinct test files).
- **Phase 4**: T027 (Node), T028/T029 (Python transport), T030 (run_example CLI), T031 (launcher) span four distinct files — can be parallelized across a small team after T025 lands.
- **Phase 5**: T033, T034, T036, T037 all in distinct files — parallel. T035 and T038 touch shared files and are sequential.
- **Phase 6**: T039, T040, T042, T043 all `[P]`.

---

## Parallel Example: User Story 1 tests

```bash
# Kick off all four US1 test files at once (pre-implementation, TDD):
Task: "T016 [US1] Loader tests in tests/windows_sample_db/test_loader.py"
Task: "T017 [US1] TCP transport tests in tests/windows_sample_db/test_transport_tcp.py"
Task: "T018 [US1] Persistence tests in tests/windows_sample_db/test_persistence.py"
Task: "T019 [US1] Happy-path procedure smoke in tests/windows_sample_db/test_procedures_happy.py"
```

## Parallel Example: Phase 2 foundational SQL

```bash
# Three independent SQL authors can work simultaneously:
Task: "T009 Overlay schema in examples/windows_sample_db/sql/00_schema_overlay.sql"
Task: "T010 Role + grants in examples/windows_sample_db/sql/01_role.sql"
Task: "T011 All 10 PL/pgSQL procedures in examples/windows_sample_db/sql/10_procedures.sql"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup (T001–T006).
2. Phase 2 Foundational (T007–T015) — bridge is TCP-only at this point.
3. Phase 3 US1 (T016–T024) — TCP example runs end-to-end; all 10 procedures invoked once over TCP; first integration test passes on Windows.
4. **STOP and VALIDATE**: run the quickstart.md TCP walk-through. Ship if approved.

### Incremental Delivery

1. MVP ships (US1).
2. US2 lands → named-pipe transport demo. `run_example --transport pipe` works.
3. US3 lands → full Windows-only test matrix, negative paths, upstream-immutability check, cleanup.
4. Polish phase.

### Parallel Team Strategy

After Phase 2 checkpoint:

- Dev A → US1 (T016–T024).
- Dev B → US2 (T025–T032) once US1's `run_example.py` is committed; Node bridge extension (T027) can start in parallel.
- Dev C → US3 test harness scaffolding (T033, T034, T036, T037) and holds T035/T038 until US1/US2 land.

---

## Notes

- `[P]` = different files and no dep on an incomplete task.
- `[Story]` maps tasks to spec.md user stories for traceability and MVP slicing.
- Tests (T016–T019, T025–T026, T033–T038) must fail before the implementation task they cover — standard TDD order.
- After every task or logical group, commit with a message referencing the task ID.
- The `pgdata/` directory is **never** committed (enforced by T004's `.gitignore` entry).
- Do not mutate upstream `country`/`airport`/etc. tables — T038 turns this into a post-run assertion (FR-023).
