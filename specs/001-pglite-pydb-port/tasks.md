---
description: "Task list for 001-pglite-pydb-port"
---

# Tasks: Port `py_pglite` to `pglite-pydb` (Cross-Platform)

**Input**: Design documents from `/specs/001-pglite-pydb-port/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/public-api.md](./contracts/public-api.md), [quickstart.md](./quickstart.md)

**Tests**: Contract tests are included in User Story 4 because they are an explicit FR-009 / Contract 8 preservation requirement for this refactor, not because a TDD stance was requested. No other test-first tasks are included.

**Organization**: Tasks are grouped by user story (US1–US5 from [spec.md](./spec.md)), with Setup, Foundational, and Polish phases wrapping them. Each user-story phase aligns to the 12-step refactor ledger in [data-model.md](./data-model.md) Entity 7 — every step from that ledger appears as one or more tasks here.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4, US5)
- Exact file paths are included in each description

## Path Conventions

- Single-project Python src-layout: `src/pglite_pydb/` (after rename), `tests/` at repo root
- Cross-platform task runner at repo root: `tasks.py`
- CI config: `.github/workflows/ci.yml`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Record the pre-refactor state and verify the working environment.

- [ ] T001 Record baseline pass count from `uv run pytest tests/ -x --tb=short` and save the trailing `X passed` number as the SC-002 invariant in `specs/001-pglite-pydb-port/BASELINE.txt`
- [ ] T002 Verify `uv sync --all-extras` succeeds on the current `main`-state working tree and confirm Node 22 is on PATH via `node --version`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Rename the module and centralise platform detection. These tasks block every user story because `src/pglite_pydb/` does not yet exist and every downstream edit targets that path.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T003 Execute `git mv src/py_pglite src/pglite_pydb` and commit as `[step 2] rename src/py_pglite → src/pglite_pydb (directory only)` — verify `git status` shows only renames with zero content changes (Step 2 of ledger)
- [ ] T004 Update `pyproject.toml` at L6 (`name = "pglite-pydb"`), L86/L91-93/L99 (optional-dep strings), L130 (pytest entry-point name), L136 (`module-name`), L164 (mypy module glob), L218 (`known-first-party`), L230 (coverage `source`); verify with `uv sync --reinstall` and `rg -nw '"py-pglite"|"py_pglite"' pyproject.toml` returning 0 hits (Step 3)
- [ ] T005 Mass-rewrite Python imports across `src/pglite_pydb/`, `tests/`, and `examples/` (which includes the existing `examples/conftest.py` and `examples/testing-patterns/*/conftest.py`): `from py_pglite` → `from pglite_pydb`, `import py_pglite` → `import pglite_pydb`, whole-word `py_pglite.` → `pglite_pydb.`; verify with `rg -nw "py_pglite" --glob "*.py"` returning 0 hits and the Linux pytest suite equalling the T001 baseline (Step 4)
- [ ] T006 [P] Create `src/pglite_pydb/_platform.py` exporting `IS_WINDOWS`, `IS_LINUX`, `IS_MACOS`, `SUPPORTS_UNIX_SOCKETS` per research.md R2 and data-model.md Entity 3; verify with `uv run python -c "from pglite_pydb._platform import IS_WINDOWS"` (Step 6)

**Checkpoint**: The package is renamed, imports are consistent, and the platform utility is available — every user story can now start.

---

## Phase 3: User Story 1 — Windows/PowerShell developer installs and runs (Priority: P1) 🎯 MVP

**Goal**: A fresh Windows 11 install of `pglite-pydb[all]` runs pytest successfully with no manual configuration, spawns the PGlite Node subprocess over TCP, and tears down cleanly with zero orphan processes.

**Independent Test**: On a clean Windows 11 VM with Python 3.12 and Node 22 LTS installed, `pip install pglite-pydb[all]` then `pytest` on a one-line `SELECT 1` fixture test passes. `Get-Process node` afterwards shows no survivors. Same test also passes on the same host running Node 20 LTS.

### Implementation for User Story 1

- [ ] T007 [US1] Add private helper `_resolve_node_bin(name: str) -> str` to `src/pglite_pydb/manager.py` implementing the `shutil.which` + Windows `.cmd`/`.exe` fallback per research.md R5; on failure raise `FileNotFoundError` listing every candidate attempted (FR-006)
- [ ] T008 [US1] Replace the bare `"npm"` string literal at `src/pglite_pydb/manager.py` L346 (`["npm", "install"]`) with `[_resolve_node_bin("npm"), "install"]` (Step 8 part 2)
- [ ] T009 [US1] Replace the bare `"node"` string literal at `src/pglite_pydb/manager.py` L393 (`["node", "pglite_manager.js"]`) with `[_resolve_node_bin("node"), "pglite_manager.js"]` (Step 8 part 3)
- [ ] T010 [US1] Modify `PGliteConfig` in `src/pglite_pydb/config.py` to distinguish user-set vs default `use_tcp`. Preferred approach: change the `use_tcp` field default from `False` to a module-level sentinel (e.g. `_UNSET = object()`) or use `dataclasses.field(default=None)` with `bool | None` typing and promote in `__post_init__`. AVOID adding a new dataclass field (even with a `_`-prefixed name) because `dataclasses.fields()` and `asdict()` would expose it; if the final implementation must add such a field, update `tests/test_public_api_contract.py::test_config_fields_preserved` to explicitly exclude private-prefixed names (`names_public = {n for n in names if not n.startswith("_")}`). Either path satisfies contracts/public-api.md Contract 3; document which was chosen in the commit message.
- [ ] T011 [US1] In `src/pglite_pydb/config.py` `__post_init__` (lines 58–83), import `from pglite_pydb._platform import IS_WINDOWS` and implement the transition table from data-model.md Entity 4: on Windows auto-promote `use_tcp` to `True` (with `logging.info()` explaining the override) and auto-assign `tcp_port = 0` if the user did not explicitly set one (Step 7 part 1, research.md R4)
- [ ] T012 [US1] In the same `__post_init__`, raise `RuntimeError` with a Windows-specific message suggesting TCP when the user explicitly passes `use_tcp=False` on Windows (FR-005, Step 7 part 2)
- [ ] T013 [US1] Expose the resolved TCP port on `PGliteManager` (e.g. set `self.port` after reading it back from the Node subprocess's startup stdout) so connection-string construction reads `manager.port` rather than `config.tcp_port` (contract Contract 3, research.md R4)
- [ ] T014 [US1] Extract a private `_terminate_process_tree(proc, timeout=5.0) -> None` helper in `src/pglite_pydb/manager.py` from the existing termination code at L507–L531, preserving the POSIX path (`os.killpg(os.getpgid(proc.pid), signal.SIGTERM)` → `wait(timeout)` → `os.killpg(..., signal.SIGKILL)`) unchanged (Step 9 part 1)
- [ ] T015 [US1] Add the Windows branch inside `_terminate_process_tree`: `psutil.Process(proc.pid).children(recursive=True)` + parent → `.terminate()` each → `proc.wait(timeout)` → call `.kill()` on any `is_running()` survivors (research.md R3, Step 9 part 2)
- [ ] T016 [US1] Update the call site at `src/pglite_pydb/manager.py` L507–L531 to delegate to `_terminate_process_tree(self.process)` and add a one-line comment at L400–L402 explaining why `preexec_fn=os.setsid` is guarded by `hasattr(os, "setsid")` (the existing check is already correct)
- [ ] T017 [US1] Windows verification — defaults: on a Windows 11 host run `uv run python -c "from pglite_pydb import PGliteConfig; c = PGliteConfig(); print(c.use_tcp, c.tcp_port)"` → expected output `True 0`; then run a minimal `SELECT 1` pytest and confirm exit status 0
- [ ] T017a [US1] Windows verification — 100-cycle no-orphan gate (SC-003): on a Windows 11 host run the PowerShell loop `$n = 0; 1..100 | % { uv run pytest tests/test_smoke.py -q; if ($LASTEXITCODE -ne 0) { $n++ } }; exit $n` and then assert `(Get-Process node -ErrorAction SilentlyContinue).Count -eq 0` and `(Get-NetTCPConnection -State TimeWait -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -ge 49152 }).Count -eq 0`; record the outputs in the PR description
- [ ] T018 [US1] Windows xdist verification spike: run `pytest -n 4 tests/test_smoke.py` ten consecutive times on Windows; confirm zero port-collision errors and zero orphan `node.exe` (SC-003a, FR-011a)

**Checkpoint**: The core Windows enablement is functional — a Windows developer can `pip install pglite-pydb[all]` and run pytest without manual config. MVP milestone for the port.

---

## Phase 4: User Story 2 — Linux/macOS user upgrades without breakage (Priority: P1)

**Goal**: An existing `py_pglite` user on Linux/macOS, after updating imports only, observes zero behavioural regressions — same default transport (Unix socket), same fixture names, same API shape, same test pass count.

**Independent Test**: On Linux, `uv run pytest tests/` at the tip of this branch yields exactly the T001 baseline pass count (SC-002). A Django or SQLAlchemy example project with nothing changed but the import lines passes unmodified (SC-007).

### Implementation / Verification for User Story 2

- [ ] T019 [P] [US2] Add a regression check task in `tasks.py` (created later in Phase 7) that compares the current `pytest tests/` pass count against `specs/001-pglite-pydb-port/BASELINE.txt` and fails the command if the counts differ — this guards SC-002 in every future iteration (note: this depends on Phase 7 `tasks.py`; if run earlier, implement as a standalone script `scripts/check_baseline.py`)
- [ ] T020 [US2] Run `uv run pytest tests/` on Linux after T006 (platform utility merged) and confirm pass count equals T001 baseline — defaults still Unix socket, no platform-conditional code touched yet
- [ ] T021 [US2] Run `uv run pytest tests/` on Linux after T013 (config.py changes merged) and confirm pass count equals T001 baseline — verifies the Windows TCP auto-promotion did not accidentally trigger on Linux (`PGliteConfig().use_tcp is False` on Linux must still hold)
- [ ] T022 [US2] Run `uv run pytest tests/` on Linux after T016 (manager.py termination changes merged) and confirm pass count equals T001 baseline — verifies the `os.killpg` path is still the one taken on POSIX
- [ ] T023 [US2] Smoke-test the SQLAlchemy example at `examples/testing-patterns/sqlalchemy/test_sqlalchemy_quickstart.py` (and the sibling `examples/testing-patterns/sqlalchemy/conftest.py`) on Linux with only the `py_pglite` → `pglite_pydb` import rewrite applied; confirm `uv run pytest examples/testing-patterns/sqlalchemy/ -q` passes without any further source edit (SC-007)
- [ ] T024 [US2] Smoke-test the Django examples under `examples/testing-patterns/django/` (at least `lightweight/` and one of `comparison/` or `full-integration/`) with the `ENGINE` setting in each `conftest.py` or test settings updated from `"py_pglite.django.backend"` to `"pglite_pydb.django.backend"` and imports rewritten; confirm `uv run pytest examples/testing-patterns/django/lightweight/ -q` passes unmodified otherwise

**Checkpoint**: Linux/macOS parity is proven. No user action beyond import updates is required for the existing platform.

---

## Phase 5: User Story 3 — CI covers both Linux and Windows (Priority: P2)

**Goal**: GitHub Actions runs the full test suite on both `ubuntu-latest` and `windows-latest` across Python 3.10–3.14, with Node 20 LTS smoke cells and xdist verification, so Windows regressions are caught at PR time.

**Independent Test**: Open a PR that deliberately breaks Windows only (e.g. hard-code a `/tmp` socket path in `manager.py`). The CI run must fail on `windows-latest` while passing on `ubuntu-latest`, proving Windows coverage is real rather than green-by-default.

### Implementation for User Story 3

- [ ] T025 [US3] Rewrite `.github/workflows/ci.yml` matrix from `runs-on: ubuntu-latest` to `runs-on: ${{ matrix.os }}`, add `os: [ubuntu-latest, windows-latest]` to the `strategy.matrix`, and extend `python-version` from `["3.10", "3.11", "3.12", "3.13"]` to `["3.10", "3.11", "3.12", "3.13", "3.14"]` per research.md R1 (Step 11 part 1)
- [ ] T026 [US3] Add Node 20 LTS smoke cells to `.github/workflows/ci.yml` via the `include:` block from research.md R11 (one cell per OS at Python 3.12 / Node 20) so both supported LTS lines are exercised without exploding the matrix (Step 11 part 2)
- [ ] T027 [US3] Add a second invocation of `pytest -n auto` after the serial `pytest` step in each matrix cell of `.github/workflows/ci.yml` with `continue-on-error: false`, so xdist regressions block merges (FR-011, FR-011a, Step 11 part 3)
- [ ] T028 [US3] Update the import smoke-check in `.github/workflows/ci.yml` L56–L57 from `python -c "import py_pglite; print(py_pglite.__version__)"` to the `pglite_pydb` equivalent, and add an analogous line `python -c "from pglite_pydb import PGliteManager, PGliteConfig"` (Step 11 part 4)
- [ ] T029 [US3] Add `shell: pwsh` to any inline shell step in `.github/workflows/ci.yml` that runs under Windows (the default is already `pwsh` on `windows-latest` but make it explicit where multi-line scripts are used)
- [ ] T030 [US3] Pre-sweep: run `rg -lw "socket_path|use_tcp=False|\.s\.PGSQL" tests/` to produce a candidate list and commit it as a checklist in the PR description. Then apply `@pytest.mark.skipif(IS_WINDOWS, reason="Unix socket transport")` (using `from pglite_pydb._platform import IS_WINDOWS`) to each matching test function (not the whole file — only the specific tests that will not pass under TCP). Reviewer must confirm the list before merge. (FR-012, Step 11 part 5)
- [ ] T031 [US3] Open a spike PR that deliberately breaks Windows (e.g. hard-code `"/tmp/pg-sock"` in `manager.py`) and confirm the Windows cell fails while Linux passes; revert the spike after the failure is visible

**Checkpoint**: Windows regressions are now caught automatically at PR time. CI matrix is 2 × 5 + 2 smoke = 12 cells, plus one xdist run per cell.

---

## Phase 6: User Story 4 — Distribution metadata matches end-to-end (Priority: P2)

**Goal**: Every surface (`pip show`, `pip install`, `pytest --trace-config`, README, CI badges, wheel filename) consistently says `pglite-pydb` / `pglite_pydb`. A tree-wide grep for the old name returns at most one hit — the intentional deprecation note.

**Independent Test**: After `uv build`, installing the wheel in a fresh virtualenv gives `Name: pglite-pydb` from `pip show pglite-pydb`, `pytest --trace-config` lists `pglite_pydb`, and `rg -nw "py_pglite|py-pglite"` across tracked files returns exactly one match (the deprecation note in `README.md`).

### Tests for User Story 4 (Contract preservation — explicitly requested by FR-009 / Contract 8)

- [ ] T032 [P] [US4] Create `tests/test_public_api_contract.py` with seven runnable test functions: the six from [contracts/public-api.md](./contracts/public-api.md) §8 (`test_top_level_imports`, `test_version_metadata_aligned`, `test_pytest_plugin_registered`, `test_config_fields_preserved`, `test_windows_rejects_explicit_unix_socket` guarded with `skipif(not IS_WINDOWS)`, `test_legacy_import_fails`) **plus** `test_node_absent_error_names_candidates` (SC-009): monkeypatch `shutil.which` to return `None`, call `pglite_pydb.manager._resolve_node_bin("node")`, assert the raised `FileNotFoundError.args[0]` contains `"node"` on all platforms and additionally `"node.cmd"` and `"node.exe"` when `IS_WINDOWS`; assert the call returns in under 2 s via `time.perf_counter()`

### Implementation for User Story 4

- [ ] T033 [P] [US4] Update `README.md` at L1, L5, L7, L9, L23, L60, L196, L202 (from Explore report) to replace `py_pglite` / `py-pglite` with `pglite_pydb` / `pglite-pydb` throughout; add the single-paragraph deprecation note from [research.md](./research.md) R9 immediately after the project description (Step 5 part 1, FR-013, SC-005, SC-010)
- [ ] T034 [P] [US4] Update `CONTRIBUTING.md` at L1, L8–9, L84 to reference the new name (Step 5 part 2)
- [ ] T035 [P] [US4] Update `.safety-project.ini` at L2–4 with the new project name (Step 5 part 3)
- [ ] T035a [P] [US4] Edit `package.json` to add `"engines": { "node": ">=20" }` per research.md R7; preserve the existing `dependencies` block; verify with `python -m json.tool package.json` (no schema errors) and that `npm install` (or `uv run python tasks.py install` after Phase 7) still succeeds
- [ ] T036 [US4] Run `rg -nw "py_pglite|py-pglite"` across all tracked files (excluding `node_modules/`, `.git/`) and verify exactly one match remains — the deprecation note in `README.md` (SC-005); fix any stragglers and re-run until the assertion holds
- [ ] T037 [US4] Run the contract test file from T032 locally (`uv run pytest tests/test_public_api_contract.py -v`) and confirm all six tests pass on Linux

**Checkpoint**: Distribution metadata is consistent end-to-end. The preservation contract from `contracts/public-api.md` is enforced by an automated test in the suite.

---

## Phase 7: User Story 5 — Contributor runs tasks on Windows PowerShell (Priority: P3)

**Goal**: A contributor on Windows invokes `uv run python tasks.py <name>` for every developer workflow (`dev`, `test`, `examples`, `lint`, `quick`, `install`, `fmt`, `clean`, `status`). Linux contributors keep using `make <target>`, which delegates to the same Python task logic — one source of truth.

**Independent Test**: On Windows PowerShell with a fresh clone, `uv run python tasks.py test` runs the suite successfully. On Linux, `make test` runs identically. On both, `uv run python tasks.py clean` removes build caches using `shutil.rmtree` (no `rm -rf` / `find -exec` anywhere in the invocation chain).

### Implementation for User Story 5

- [ ] T038 [P] [US5] Create `tasks.py` at the repo root with an `argparse`-based dispatcher per research.md R6; implement one function per task (`task_dev`, `task_test`, `task_examples`, `task_lint`, `task_quick`, `task_install`, `task_fmt`, `task_clean`, `task_status`); each function takes `argv: list[str]` and returns an `int` exit code; stdlib-only, no third-party deps (Step 10 part 1)
- [ ] T039 [US5] Implement `task_clean` in `tasks.py` using `shutil.rmtree(path, ignore_errors=True)` against the standard Python cache paths (`build/`, `dist/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `htmlcov/`, `coverage.xml`, `*.egg-info/`); use `pathlib.Path.rglob` to locate `__pycache__/` recursively — NEVER shell out to `rm` or `find` (FR-014)
- [ ] T040 [US5] Rewrite every target in `Makefile` to be a single-line delegation: `<target>:\n\tuv run python tasks.py <target>`; preserve the `.PHONY:` declarations and the existing help text (Step 10 part 2)
- [ ] T041 [US5] Add a "Windows quickstart" section to `CONTRIBUTING.md` documenting `uv run python tasks.py <name>` as the primary Windows invocation and `make <name>` as the Linux/macOS equivalent, noting that both dispatch to the same Python code (Q1 clarification)
- [ ] T042 [US5] Verify on Linux that `make test`, `make lint`, `make clean`, `make status` behave identically to the pre-refactor `Makefile` (compare stdout spot-checks); this proves the delegation layer did not introduce regressions for the existing Linux workflow
- [ ] T043 [US5] Verify on Windows PowerShell that `uv run python tasks.py test`, `uv run python tasks.py lint`, `uv run python tasks.py clean`, `uv run python tasks.py status` all succeed with no PATH-resolution or execution-policy errors

**Checkpoint**: Windows contributors have first-class parity with Linux contributors. The task runner is the single source of truth.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Release-verification and final cleanup across all stories.

- [ ] T044 Bump the `version` field in `pyproject.toml` to `0.6.0` (FR-016)
- [ ] T045 Run `uv build` and confirm `dist/pglite_pydb-0.6.0-py3-none-any.whl` and `dist/pglite_pydb-0.6.0.tar.gz` are produced (Step 12 part 1, SC-008)
- [ ] T046 [P] Fresh-venv Linux release verification: `python -m venv .venv-verify && source .venv-verify/bin/activate && pip install "dist/pglite_pydb-0.6.0-py3-none-any.whl[all]" && pytest examples/quickstart/` succeeds; then `pip show pglite-pydb` reports `Name: pglite-pydb` and `pytest --trace-config` lists `pglite_pydb`
- [ ] T047 [P] Fresh-venv Windows PowerShell release verification: identical flow under `.\.venv-verify\Scripts\Activate.ps1`; smoke test from `examples/quickstart/` must complete in under 5 minutes (SC-001)
- [ ] T048 Final tree-wide check: `rg -nw "py_pglite|py-pglite"` returns exactly one hit (the deprecation note in `README.md`); any additional hits are regressions and MUST be fixed before tagging (SC-005)
- [ ] T048a Verify FR-017 preservation: confirm `pyproject.toml` still pins `Django>=5.2.8` (the constraint that carries the SQL-injection fix from commit `31bc3a2`) via `rg -n "Django" pyproject.toml`; confirm the existing `safety` CI job (`.github/workflows/ci.yml` `security:` block) is unchanged and still runs against the renamed distribution
- [ ] T049 Run the full contract test file `tests/test_public_api_contract.py` on both Linux and Windows CI cells from the PR; all six tests MUST pass on both OSes (Contract 8 gate)
- [ ] T050 Tag release: `git tag -a v0.6.0 -m "pglite-pydb 0.6.0: renamed from py-pglite, cross-platform (Linux + Windows)"` (local); do **not** push the tag until the maintainer signs off (destructive action per project guidance)
- [ ] T051 Verify the commit history on the branch consists of exactly 12 step-aligned commits (or a small, documented multiple thereof), each leaving `pytest tests/` green on Linux when checked out in isolation (SC-006)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup; BLOCKS every user-story phase (the `src/pglite_pydb/` directory does not exist until T003 and `_platform.py` does not exist until T006)
- **User Story 1 (Phase 3, P1)**: depends on Foundational; core Windows enablement; the MVP
- **User Story 2 (Phase 4, P1)**: depends on Foundational; runs in parallel with US1 (verification-heavy — it asserts that US1's code changes did not break Linux)
- **User Story 3 (Phase 5, P2)**: depends on US1 being merged at least in skeleton form (CI needs real code to exercise); can start in parallel with US4
- **User Story 4 (Phase 6, P2)**: depends on Foundational; can run fully in parallel with US1 and US3 (touches docs + a new test file, no code collisions)
- **User Story 5 (Phase 7, P3)**: depends on Foundational only; fully parallel with US1/US3/US4
- **Polish (Phase 8)**: depends on US1–US5 all being complete and merged

### Within Each User Story

- Models / config edits (T007, T010, T011) before manager.py call-site swaps (T008, T009, T013)
- Helper extraction (T014, T007) before helper-calling code (T015, T008)
- CI matrix edit (T025) before CI smoke-check update (T028) and xdist addition (T027)
- `tasks.py` creation (T038) before Makefile delegation rewrite (T040)
- Contract test file (T032) before running it (T037)
- `version` bump (T044) before `uv build` (T045)

### Parallel Opportunities

**Within Foundational**: T006 (`_platform.py` creation) can run in parallel with the later half of T005 (test-suite-run verification); T003/T004/T005 are strictly sequential.

**Across user stories once Foundational is done**:

- US1 (code) + US4 (docs + test) + US5 (task runner) can be worked on by three developers simultaneously
- US2 (verification) reviews US1's work but does not block it
- US3 (CI) follows once US1 is mergeable

**Within US4**: T033, T034, T035 touch disjoint files and are all `[P]`-safe.

**Within Polish**: T046 (Linux verify) and T047 (Windows verify) are `[P]` (run on separate hosts).

---

## Parallel Example: User Story 1 (Windows enablement)

```bash
# Once Foundational (T001-T006) is done, these three task-groups can proceed
# in parallel on different branches or by different developers:

# Developer A — config.py platform-conditional defaults
Task: "Modify PGliteConfig to track explicit use_tcp (T010) in src/pglite_pydb/config.py"
Task: "Implement Windows TCP auto-select in __post_init__ (T011)"
Task: "Raise RuntimeError on explicit use_tcp=False under Windows (T012)"

# Developer B — manager.py binary resolution
Task: "Add _resolve_node_bin helper (T007) in src/pglite_pydb/manager.py"
Task: "Swap npm string literal for resolver call (T008)"
Task: "Swap node string literal for resolver call (T009)"

# Developer C — manager.py process-tree termination
Task: "Extract _terminate_process_tree helper, POSIX path unchanged (T014)"
Task: "Add Windows psutil branch inside _terminate_process_tree (T015)"
Task: "Update call site to use helper (T016)"
```

---

## Implementation Strategy

### MVP First (User Story 1 + minimum US2 verification)

1. Complete Phase 1 (Setup): T001, T002
2. Complete Phase 2 (Foundational): T003 → T004 → T005 → T006 — strictly sequential; each commit must leave Linux tests green
3. Complete Phase 3 (User Story 1): T007–T018 — core Windows enablement code
4. Run Phase 4 (User Story 2) verification: T020–T022 at minimum — prove Linux is not regressed
5. **STOP and VALIDATE** — at this point, a Windows developer can manually install from a wheel built off the branch and run tests. The MVP is real.

### Incremental Delivery (recommended ordering)

1. Foundational → commit per step → green on Linux each time
2. US1 code + US2 minimum verification → manual Windows smoke works → first Windows-capable internal build
3. US4 (docs + contract test) → public-facing consistency ready
4. US3 (CI) → Windows regressions caught automatically
5. US5 (task runner) → contributor ergonomics complete
6. Polish → version bump, release verification, tag → 0.6.0

Each of these is a commit-boundary and a reviewer checkpoint.

### Parallel Team Strategy

- Developer A: US1 (Windows code changes, T007–T018)
- Developer B: US4 (docs + contract test, T032–T037) and US5 (task runner, T038–T043) in sequence
- Developer C: US3 (CI matrix) after US1 lands skeleton code in the main branch of the feature
- Maintainer: reviews + runs US2 verification tasks (T020–T024) after each merge

---

## Notes

- [P] tasks touch disjoint files and have no dependency on any other in-flight task
- [Story] label maps each task to the user story it delivers against (traceability with spec.md priorities)
- Every commit on the branch MUST leave `uv run pytest tests/` green on Linux — SC-006 gate, enforced by pre-push check in the future `tasks.py test` target
- Windows verification (T017, T018, T031, T043, T047) requires a real Windows host (VM or GitHub Actions `windows-latest` runner); it is not mockable
- The 12-step ledger in [data-model.md](./data-model.md) Entity 7 maps to tasks as: Step 1 → T001; Step 2 → T003; Step 3 → T004; Step 4 → T005; Step 5 → T033/T034/T035/T035a; Step 6 → T006; Step 7 → T010/T011/T012/T013; Step 8 → T007/T008/T009; Step 9 → T014/T015/T016; Step 10 → T038/T039/T040/T041; Step 11 → T025/T026/T027/T028/T029/T030; Step 12 → T044–T051 plus T048a. Verification tasks not tied to a specific step: T002, T017, T017a, T018, T020–T024, T031, T037, T042, T043, T046, T047, T049
- Avoid: rewriting imports before `pyproject.toml` (causes metadata drift per research.md R10); committing `node_modules/` (already `.gitignored`); pushing `v0.6.0` tag before maintainer review
