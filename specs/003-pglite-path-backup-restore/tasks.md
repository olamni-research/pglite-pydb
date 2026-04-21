# Tasks: Mandatory Data Path + Backup/Restore Commands

**Input**: Design documents from `/specs/003-pglite-path-backup-restore/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/cli.md ✓, contracts/manifest.md ✓, quickstart.md ✓

**Tests**: INCLUDED — FR-029 explicitly mandates automated tests on both Linux and Windows for every capability in this feature.

**Organization**: Tasks are grouped by user story so each story (US1 mandatory path → US2 backup → US3 restore) can be implemented, tested, and shipped as an independent increment. Full-snapshot mode is folded into US2 (backup side) and US3 (restore side) rather than given its own top-level phase, because spec/plan treat it as a parallel track layered on top of those stories, not a separate story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1, US2, US3 for story-specific tasks; no label for Setup / Foundational / Polish
- Every task names an exact file path under `D:\bstdev\pglite\`

## Path Conventions

Single-project Python src-layout (inherited from feature 001):

- Package source: `src/pglite_pydb/`
- Tests: `tests/`
- Packaging: `pyproject.toml` at repo root
- Docs pointer: `CLAUDE.md` at repo root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Wire up packaging-level plumbing that every subsequent phase will touch. No business logic yet.

- [X] T001 Add `[project.scripts] pglite-pydb = "pglite_pydb.cli.main:main"` entry in `pyproject.toml` so the console script is registered on install (contracts/cli.md §Top-level).
- [X] T002 Create empty package skeleton: `src/pglite_pydb/cli/__init__.py` (exports `main`) and a stub `src/pglite_pydb/cli/main.py` with an `argparse`-based `main()` that prints usage and exits 2. No subcommand logic yet — just the scaffolding the entry point binds to.
- [X] T003 [P] Create empty stub `src/pglite_pydb/_lock.py` with a module-level docstring and a `TODO(T010)` placeholder (keeps imports in T004/T007 resolvable while real logic lands in Phase 2).
- [X] T004 [P] Create empty stub `src/pglite_pydb/backup.py` with a module-level docstring and a `TODO(T022..)` placeholder (same rationale).
- [X] T005 [P] Add pytest markers `windows_only` and `requires_pg_dump` to `pyproject.toml` under `[tool.pytest.ini_options]` markers list, and a conftest helper `tests/conftest.py` that registers `pytest.skip` for `requires_pg_dump` when `shutil.which("pg_dump")` is `None` (research §R1, plan Testing section).

**Checkpoint**: `pip install -e .` succeeds, `pglite-pydb --help` runs (argparse usage), `pytest --collect-only` runs clean.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Land the primitives every user story depends on — resolved data-dir semantics, the cross-platform instance lock, the sidecar config reader/writer, and the shared confirmation helper. Nothing here is story-specific, so nothing goes into US1/US2/US3 until this phase is green.

**⚠️ CRITICAL**: No US1/US2/US3 task may begin until this phase is complete.

### Lock primitive

- [X] T006 [P] Write failing unit tests for cross-platform advisory lock in `tests/test_instance_lock.py` covering: (a) first acquire succeeds, (b) second acquire in same process raises `InstanceInUseError`, (c) second acquire from a subprocess raises `InstanceInUseError` with the resolved path in the message, (d) release on context exit allows subsequent acquire, (e) kill-9 / SIGKILL equivalent releases the lock (POSIX only — add `@pytest.mark.skipif(IS_WINDOWS, ...)`), (f) Windows-only test via `msvcrt.locking` marked `@pytest.mark.windows_only`. Maps FR-006.
- [X] T007 Implement `InstanceLock` context manager in `src/pglite_pydb/_lock.py` per data-model.md §4 and research §R2: lock file at `<data-dir>/.pglite-pydb/instance.lock`; `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on POSIX, `msvcrt.locking(fd, LK_NBLCK, 1)` on Windows, dispatched from `_platform.IS_WINDOWS`; non-blocking; raises `InstanceInUseError(resolved_data_dir)` on contention; releases on `__exit__` and OS-level on process death. Make T006 pass.

### Error taxonomy

- [X] T008 [P] Add exception class hierarchy in `src/pglite_pydb/errors.py` (NEW file) covering every class in data-model.md §10: `MissingDataDirError`, `InvalidDataDirError`, `InstanceInUseError`, `BackupLocationNotConfiguredError`, `BackupLocationUnavailableError`, `SchemaNotFoundError`, `NoBackupsFoundError`, `BackupSelectorMissingError`, `ContainerKindMismatchError`, `CorruptContainerError`, `RestoreConflictError`, `ConfirmationRequiredError`, `ConfirmationDeclinedError`. Each carries the offending argument/path/schema in its message (FR-030). Re-export from `src/pglite_pydb/__init__.py`.

### Sidecar config

- [X] T009 [P] Write failing unit tests for the sidecar config in `tests/test_sidecar_config.py`: (a) `load()` on a missing sidecar returns a default config with `backup_location=None` and does NOT create the file, (b) `save()` creates `<data-dir>/.pglite-pydb/` and writes JSON with `schema_version: 1`, (c) round-trip preserves `backup_location`, (d) `load()` on `schema_version` other than 1 raises with an "upgrade pglite-pydb" hint, (e) JSON is UTF-8 with 2-space indent and sorted keys (manifest contract's forward-compat rule applies to the sidecar for the same reason). Maps FR-008..FR-011.
- [X] T010 Implement `SidecarConfig` dataclass with `load(data_dir)` / `save(data_dir)` in `src/pglite_pydb/config.py` per data-model.md §3. Extend (do NOT rewrite) the existing `PGliteConfig` class: add mandatory `data_dir: Path` field with resolve-and-validate in `__post_init__` (follows symlinks via `Path.resolve(strict=False)` per FR-003; rejects files / non-empty non-PGlite dirs per FR-005; raises `MissingDataDirError` if absent). Make T009 pass.

### Confirmation helper

- [X] T011 [P] Write failing unit tests in `tests/test_confirm_helper.py` for the shared confirmation helper covering the 4-cell TTY × `--assume-yes` matrix from research §R6, plus the FR-035 second-confirmation split (`--assume-yes-destroy` is required separately in non-TTY). Use `monkeypatch` to stub `sys.stdin.isatty` and `input`.
- [X] T012 Implement `_confirm(prompt, *, assume_yes)` and `_confirm_destroy(prompt, *, assume_yes_destroy)` in `src/pglite_pydb/cli/_confirm.py` (NEW file) per research §R6. Fail-fast raises `ConfirmationRequiredError` in non-TTY without the right flag; raises `ConfirmationDeclinedError` on user "no". Make T011 pass.

### Data-directory predicates

- [X] T013 [P] Implement the `DataDirectory` predicates from data-model.md §2 as module-level functions in `src/pglite_pydb/config.py` (or a new `src/pglite_pydb/_datadir.py` — choose based on where `PGliteConfig.__post_init__` needs them): `is_fresh`, `is_existing_pglite_instance`, `is_rejectable`, `is_completely_empty_for_full_snapshot_restore` (research §R7 allow-list: `.pglite-pydb/`, `.DS_Store`, `Thumbs.db`, `desktop.ini`; top-level scan only). Used by T010 and later by T035 (full-snapshot restore).

### Wrapper integration of mandatory data-dir

- [X] T014 Update `src/pglite_pydb/manager.py`: rename `_setup_work_dir` to `_prepare_data_dir`; remove the ephemeral-temp-dir default; on `start()` acquire `InstanceLock` before spawning Node; embed the resolved `data_dir` into the generated `pglite_manager.js` as `new PGlite("file://" + dataDir, {...})` (plan Summary §1); on first start, create `<data-dir>/.pglite-pydb/` and write the sidecar (empty `backup_location`) if not already present. Release lock on `stop()` / context exit.
- [X] T015 Update `src/pglite_pydb/fixtures.py`, `src/pglite_pydb/pytest_plugin.py`, `src/pglite_pydb/django/…`, and `src/pglite_pydb/sqlalchemy/…` so every fixture that constructs a `PGliteConfig` now passes a `tmp_path`-derived `data_dir`. No public rename; just the new required argument (plan.md Source Code section).

**Checkpoint**: Foundation ready — `pytest tests/test_instance_lock.py tests/test_sidecar_config.py tests/test_confirm_helper.py` green on both Linux and Windows. US1/US2/US3 phases can now begin (and can be worked in parallel by different people, since their files don't overlap).

---

## Phase 3: User Story 1 — Explicit Data Directory for PGlite Instance (Priority: P1) 🎯 MVP

**Goal**: Every `pglite-pydb` PGlite instance persists exclusively to an explicit, resolved, mandatory data directory; reuse of the same path reopens without overwrite; concurrent starts on the same path fail fast.

**Independent Test**: Start with a fresh path → create table + rows → stop → restart with same path → rows preserved. Start with missing path arg → clear error, no process. Start with path-is-a-file or path-has-unrelated-content → clear error, target unchanged. Start two instances concurrently with the same path → second fails with "instance in use". Filesystem scan: 0 PGlite files outside the supplied path.

### Tests for User Story 1 ⚠️ (write first, ensure they fail, then implement)

- [X] T016 [P] [US1] Integration test `tests/test_data_dir_mandatory.py::test_missing_data_dir_fails` — constructing `PGliteConfig()` without `data_dir` raises `MissingDataDirError`; no PGlite subprocess is spawned. Maps FR-001, SC-001.
- [X] T017 [P] [US1] Integration test `tests/test_data_dir_mandatory.py::test_fresh_path_initialises_only_inside` — start the wrapper at a tmp_path subdirectory; assert a `PG_VERSION` file exists inside it; walk `tmp_path.parent` (the mktemp root) and assert no PGlite files appear outside the supplied subdir. Maps FR-002, FR-004, SC-002.
- [X] T018 [P] [US1] Integration test `tests/test_data_dir_mandatory.py::test_existing_path_preserves_data` — start, create 3 schemas with 10 rows each, stop, restart, assert all 30 rows present and no reinitialisation marker changed. Maps FR-004, SC-003.
- [X] T019 [P] [US1] Integration test `tests/test_data_dir_mandatory.py::test_rejectable_paths` — parametrised over: (a) path points at a regular file, (b) path points at a non-empty dir with unrelated content, (c) path is not writable. Each raises `InvalidDataDirError` with the specific sub-reason in the message; target location is byte-unchanged after the failure. Maps FR-005.
- [X] T020 [P] [US1] Integration test `tests/test_data_dir_mandatory.py::test_concurrent_start_same_path_fails_fast` — spawn a subprocess holding a wrapper open on a path; from the parent, attempt a second start on the same resolved path; assert `InstanceInUseError` within 100 ms (plan Performance Goals) and that the subprocess's instance is unaffected. Maps FR-006.
- [X] T021 [P] [US1] Integration test `tests/test_data_dir_mandatory.py::test_symlink_resolves_to_same_instance` — create a symlink that points at the data dir; start via the symlink and separately via the real path; assert both resolve to the same `InstanceLock` (second attempt fails with the real path in the error message). Maps FR-003 + edge-cases list.

### Implementation for User Story 1

All of US1's production code lands in Phase 2 (T014/T015 are load-bearing). If T016–T021 turn up residual gaps, those gaps are fixed in `src/pglite_pydb/manager.py` / `src/pglite_pydb/config.py`; no new source files are introduced in this phase.

- [X] T022 [US1] Fix any residual failures from T016–T021 in `src/pglite_pydb/manager.py` and `src/pglite_pydb/config.py`. Do NOT create new modules here — this task is only to close the gap between Phase-2 implementation and the US1 acceptance tests.

**Checkpoint**: US1 is shippable on its own as MVP. All five Acceptance Scenarios from spec.md US1 map to a passing test. SC-001, SC-002, SC-003 measurable outcomes pass on Linux and Windows CI matrix entries.

---

## Phase 4: User Story 2 — Backup One, Some, or All Databases/Schemas (Priority: P2)

**Goal**: `pglite-pydb backup` writes a new timestamp-named `.tar.gz` container into the instance's configured backup location in any of the three logical selection modes OR as a full-snapshot, acquiring the FR-006 lock by default, with `--force-hot` for best-effort logical dumps against a running server. No overwrite of existing containers; each invocation self-describes via `manifest.json`.

**Independent Test**: Configure a backup location for a US1-validated instance, populate 2 schemas with known rows, run `pglite-pydb backup` in three logical modes plus `--full-snapshot` at four different timestamps, and assert four distinct containers exist in the location with correct manifests and filename shapes. Restore is out of scope here — US3 proves restorability.

### Tests for User Story 2 ⚠️ (all marked `requires_pg_dump` where appropriate)

- [X] T023 [P] [US2] CLI contract test `tests/test_cli_backup_args.py` — parametrised over invalid flag combinations (no selector; `--schema` + `--all`; `--schema` + `--full-snapshot`; `--force-hot` + `--full-snapshot`; `--force-hot` without a logical selector); each exits 2 with an argparse usage message naming the offending flag. Maps contracts/cli.md `backup` grammar.
- [X] T024 [P] [US2] Integration test `tests/test_backup_logical.py::test_single_schema` — `requires_pg_dump`. Run `pglite-pydb backup --data-dir <d> --schema app`; assert one new `YYYYMMDD-HHMMSS.fff.tar.gz` in the backup location; `tar -tzf` shows `<ts>/manifest.json` and `<ts>/app.sql`; manifest has `kind=="logical"`, `included_schemas==["app"]`, `schema_version==1`, correct `source_data_dir`, `created_at` matches filename ts, `container_filename` matches actual filename. Maps FR-013(a), FR-014, FR-016, manifest contract.
- [X] T025 [P] [US2] Integration test `tests/test_backup_logical.py::test_list_of_schemas` — `--schema app --schema analytics`; one container; two `.sql` entries; `included_schemas` order matches first-seen CLI order. Maps FR-013(b).
- [X] T026 [P] [US2] Integration test `tests/test_backup_logical.py::test_all_mode` — `--all`; one container; one `.sql` per user schema (excluding `pg_catalog`, `information_schema`, `pg_toast`, pglite internals per data-model.md §8); manifest `included_schemas == ["*"]`. Maps FR-013(c).
- [X] T027 [P] [US2] Integration test `tests/test_backup_logical.py::test_all_on_schemaless_instance_produces_valid_empty_container` — fresh instance with zero user schemas; `--all` still produces a container with `included_schemas==["*"]` and zero `.sql` entries (edge case from spec.md).
- [X] T028 [P] [US2] Integration test `tests/test_backup_logical.py::test_missing_schema_fails_with_no_partial` — `--schema does_not_exist` exits 8 with `SchemaNotFoundError` naming the schema; backup location contains zero `.tar.gz` files and zero `.partial` files. Maps FR-015, FR-017 cleanup.
- [X] T029 [P] [US2] Integration test `tests/test_backup_logical.py::test_no_backup_location_configured_fails` — fresh instance without `config set-backup-location`; `backup` exits 6 with `BackupLocationNotConfiguredError` whose message contains the `pglite-pydb config set-backup-location` invocation hint. Maps FR-010.
- [X] T030 [P] [US2] Integration test `tests/test_backup_logical.py::test_unwritable_location_fails` — configured location set to a read-only dir (chmod 555 on POSIX; DENY ACL on Windows via `icacls`); `backup` exits 7 with `BackupLocationUnavailableError` naming the location; no half-written files appear in the location. Maps FR-011 + edge-cases list.
- [X] T031 [P] [US2] Integration test `tests/test_backup_logical.py::test_rapid_fire_10x_unique` — launch 10 `pglite-pydb backup --all --force-hot` invocations concurrently from a single shared running instance; wait for all; assert exactly 10 distinct `.tar.gz` files exist; names sort chronologically; no `_<n>` collisions OR if present, each `_<n>` suffix disambiguates cleanly. Must complete in < 60 s wall-clock (plan Performance Goals, SC-008).
- [X] T032 [P] [US2] Integration test `tests/test_backup_logical.py::test_default_requires_exclusive_lock` — start an instance (holds the lock), then from the same process but a subprocess invoke `pglite-pydb backup` without `--force-hot`; assert exit 5 `InstanceInUseError`. With `--force-hot`, the same scenario succeeds and produces a valid container. Maps FR-017.
- [X] T033 [P] [US2] Integration test `tests/test_backup_full_snapshot.py::test_full_snapshot_layout_and_sidecar_exclusion` — `--full-snapshot` produces `FULL_SNAPSHOT_<ts>.tar.gz`; archive contains `<archivename>/manifest.json` (kind=="full-snapshot") and `<archivename>/data/...` with the full data-dir file tree EXCEPT `.pglite-pydb/`; assert that subtree is absent from the tar listing. Maps FR-031, FR-032, manifest contract.
- [X] T034 [P] [US2] Integration test `tests/test_backup_full_snapshot.py::test_full_snapshot_requires_lock_no_force_hot` — running instance holds the lock; `backup --full-snapshot` exits 5; there is no `--force-hot` flag for full-snapshot (argparse rejects the combination with exit 2). Maps FR-033.
- [X] T035 [P] [US2] CLI contract test `tests/test_cli_config.py` — `pglite-pydb config set-backup-location` writes the sidecar (resolved absolute path, does not require the directory to exist at config time); `get-backup-location` prints it or `(not configured)`; `show` prints pretty JSON. Maps contracts/cli.md `config`, FR-009.

### Implementation for User Story 2

- [X] T036 [US2] Implement `pglite-pydb config` subcommand group in `src/pglite_pydb/cli/main.py` (and optionally split into `src/pglite_pydb/cli/_config_cmd.py`): `set-backup-location`, `get-backup-location`, `show`. Does NOT acquire `InstanceLock`; only touches the sidecar via `SidecarConfig` from T010. Make T035 pass.
- [X] T037 [US2] Implement timestamp helper `src/pglite_pydb/utils.py::utc_timestamp_filename()` producing `YYYYMMDD-HHMMSS.fff` UTC per research §R3; include the `_<n>` disambiguation routine that, given a proposed filename and an existing-files iterable, returns the smallest unused suffix.
- [X] T038 [US2] Implement `BackupEngine.create_logical(instance, selection, *, force_hot=False)` in `src/pglite_pydb/backup.py` per data-model.md §9a and research §R1/R4: start a PGlite TCP server (default under `InstanceLock`; skipped under `--force-hot` to attach to a running server), run `pg_dump --schema=<name> --format=plain --no-owner --no-privileges` per included schema (respecting `PGLITE_PYDB_PG_DUMP` env override), write into a `<final>.partial` tarball with `tarfile.PAX_FORMAT`, write `manifest.json` last, atomic-rename `.partial` → final; on ANY error delete the `.partial` and re-raise; resolve "all" selection via a live catalog query (`SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN (...)`). Make T024–T032 pass.
- [X] T039 [US2] Implement `BackupEngine.create_full_snapshot(instance)` in `src/pglite_pydb/backup.py` per data-model.md §9b: acquire `InstanceLock` (no force-hot possible), walk the data directory excluding the top-level `.pglite-pydb/` subtree, write to a `FULL_SNAPSHOT_<ts>.tar.gz.partial` via stdlib `tarfile` in PAX format, write `manifest.json` with `kind=="full-snapshot"`, atomic-rename; on failure delete partial. Make T033, T034 pass.
- [X] T040 [US2] Wire `pglite-pydb backup` subcommand in `src/pglite_pydb/cli/main.py` per contracts/cli.md: argument parsing (mutually exclusive group for `--schema...`/`--all`/`--full-snapshot`; `--force-hot` validation); print the two-line "instance data dir / backup location" banner to stderr on start (contracts/cli.md); print the resolved container path on stdout on success; map every exception from T008 to its stable exit code from the contract table. Make T023 pass and close any residual gaps in T024–T034.
- [X] T041 [US2] Add `pg_dump` / `psql`-missing-on-PATH handling: a single helper `src/pglite_pydb/_pgtools.py::resolve_pg_dump()` / `resolve_psql()` that consults `PGLITE_PYDB_PG_DUMP` / `PGLITE_PYDB_PSQL` env vars, falls back to `shutil.which`, and raises a clean exit-1 error with the actionable message from contracts/cli.md if neither yields a binary. Used by T038 and later T048.

**Checkpoint**: US2 is shippable on top of US1. An operator can: configure a backup location, produce logical containers in all three selection modes, produce full-snapshot containers, inspect manifests via `tar -xOzf ... manifest.json`, and get actionable errors in every documented failure mode. SC-004 (backup success for representative fixtures) and SC-008 (10-rapid-fire distinct containers) both pass.

---

## Phase 5: User Story 3 — Restore One or More Databases from Backups (Priority: P3)

**Goal**: `pglite-pydb restore` reconstitutes data into a mandatory-path target instance from named logical containers or `--latest` (scoped to logical), OR from a named full-snapshot container or `--full-snapshot --latest` (scoped to full-snapshot). All interactive prompts follow the FR-022/FR-025/FR-035 TTY + `--assume-yes[-destroy]` contract. Kind-cross-selection is refused (FR-034).

**Independent Test**: Given the containers produced in US2 across multiple timestamps, recreate a fresh target instance, then exercise: restore by name (single and list); `restore --latest` with y/n confirmation; `restore` with no selector (fails with FR-020 error); overwrite conflict path both without and with `--overwrite`; corrupt container rejection; `restore --full-snapshot` with single-confirmation (empty target) and two-confirmation (non-empty target) flows; kind-mismatch rejection in both directions; sidecar preservation after full-snapshot restore.

### Tests for User Story 3 ⚠️ (all `requires_pg_dump` since psql is part of the restore path)

- [X] T042 [P] [US3] CLI contract test `tests/test_cli_restore_args.py` — invalid combinations: no selector and no `--latest` (exit 10 `BackupSelectorMissingError`); `<container>` + `--latest` (exit 2); `--full-snapshot` + `--overwrite` (exit 2); `--full-snapshot` with neither `<container>` nor `--latest` (exit 2 — mode requires one). Maps FR-020, contracts/cli.md `restore` grammar.
- [X] T043 [P] [US3] Integration test `tests/test_restore_logical.py::test_by_name_single_container` — empty target; restore a US2-produced `<ts>.tar.gz`; target contains the restored schema's tables/rows; other schemas untouched. Maps FR-019(a), FR-024(a).
- [X] T044 [P] [US3] Integration test `tests/test_restore_logical.py::test_by_name_list_of_containers` — two separate US2 containers; single `restore` invocation with both; target ends with the union of their schemas. Maps FR-024(b).
- [X] T045 [P] [US3] Integration test `tests/test_restore_logical.py::test_latest_with_tty_confirmation` — use a PTY (POSIX) or stub `sys.stdin.isatty()` to simulate TTY; assert `--latest` picks the lexically-highest `<ts>.tar.gz` in the configured location (ignoring any `FULL_SNAPSHOT_*` files present — FR-021 scoping); prompt appears on stderr showing ts + included_schemas; typing `y` proceeds, typing `n` exits 15 `ConfirmationDeclinedError` with no target changes. Maps FR-021.
- [X] T046 [P] [US3] Integration test `tests/test_restore_logical.py::test_latest_non_tty_requires_assume_yes` — non-TTY stdin; `--latest` without `--assume-yes` exits 14 `ConfirmationRequiredError`; with `--assume-yes` proceeds. Maps FR-022.
- [X] T047 [P] [US3] Integration test `tests/test_restore_logical.py::test_no_backups_vs_no_location_distinct_errors` — (a) location configured but empty: `--latest` exits 9 `NoBackupsFoundError`; (b) location not configured: `--latest` exits 6 `BackupLocationNotConfiguredError`. Messages distinguish the two. Maps FR-023.
- [X] T048 [P] [US3] Integration test `tests/test_restore_logical.py::test_overwrite_conflict_and_flow` — target already has schema `app`; restore without `--overwrite` exits 13 `RestoreConflictError` naming `[app]`; with `--overwrite` and TTY, prompt lists `[app]` and proceeds on `y`; with `--overwrite --assume-yes` in non-TTY, proceeds; with `--overwrite` alone in non-TTY, exits 14. Other schemas in target remain untouched across all paths. Maps FR-025.
- [X] T049 [P] [US3] Integration test `tests/test_restore_logical.py::test_corrupt_container_rejected` — parametrised: (a) truncated `.tar.gz`, (b) valid tar but malformed `manifest.json`, (c) manifest `schema_version` > 1, (d) manifest `kind == "full-snapshot"` in a non-`FULL_SNAPSHOT_*` filename; each exits 12 `CorruptContainerError` naming the offending container and the specific reason; target is byte-unchanged. Maps FR-026.
- [X] T050 [P] [US3] Integration test `tests/test_restore_logical.py::test_atomicity_on_mid_restore_failure` — restore a container whose SQL triggers a mid-stream error (e.g. deliberately inject invalid DDL via a fixture); assert the target's pre-existing schemas are completely unchanged (per-container transactional application, data-model §9c). Maps FR-027.
- [X] T051 [P] [US3] Integration test `tests/test_restore_logical.py::test_missing_data_dir_fails` — omit `--data-dir`; exits 3 `MissingDataDirError`. Maps FR-028.
- [X] T052 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_full_snapshot_by_name_into_empty_target` — target directory does not exist; `restore --full-snapshot <container>` with TTY + `y` confirmation OR with `--assume-yes` in non-TTY succeeds; target now contains the source's data tree; source instance's sidecar is NOT present in the target. Maps FR-034, FR-035 (empty-target single confirmation), FR-036 (sidecar preservation = none-before, none-after).
- [X] T053 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_full_snapshot_latest_scoping` — mix of logical and `FULL_SNAPSHOT_*` containers in the location; `restore --full-snapshot --latest` picks the lexically-highest `FULL_SNAPSHOT_*` (ignoring logical ones); conversely `restore --latest` picks the logical one (ignoring full-snapshots). Maps FR-021 + FR-034 scoping.
- [X] T054 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_full_snapshot_two_stage_over_non_empty_target` — target already has data (e.g. US2-produced state). TTY path: first prompt `y` → second prompt must appear (the "type DESTROY" variant from contracts/cli.md); typing `DESTROY` proceeds; typing anything else aborts with exit 15 and zero target changes. Non-TTY path: `--assume-yes` alone exits 14 on the second prompt; `--assume-yes --assume-yes-destroy` proceeds. Maps FR-035.
- [X] T055 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_completely_empty_allow_list` — parametrised target state: empty dir, dir with only `.pglite-pydb/`, dir with only `.DS_Store`, dir with only `Thumbs.db`, dir with only `desktop.ini`, dir with any combination of those. All are treated as "completely empty" → single confirmation only. Any non-allow-listed entry → second confirmation fires. Maps FR-035 + research §R7.
- [X] T056 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_sidecar_preserved_and_none_created` — (a) target has its own sidecar with a different `backup_location`: after full-snapshot restore, the target's sidecar is byte-identical to the pre-restore state (the source's sidecar was excluded from the archive); (b) target has no sidecar pre-restore: after restore, target still has no sidecar; next `backup` on the target fails with `BackupLocationNotConfiguredError` until `config set-backup-location` is run. Maps FR-036 + clarification.
- [X] T057 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_kind_mismatch_rejected_both_directions` — passing a `FULL_SNAPSHOT_*.tar.gz` to `restore` (logical mode) exits 11 `ContainerKindMismatchError` with the container kind in the message; passing a `<ts>.tar.gz` to `restore --full-snapshot` exits 11 symmetrically. Maps FR-034.
- [X] T058 [P] [US3] Integration test `tests/test_restore_full_snapshot.py::test_acquires_lock` — start a wrapper holding the target (US1 lock); `restore --full-snapshot` against the same resolved path exits 5 `InstanceInUseError`. Maps FR-036.

### Implementation for User Story 3

- [X] T059 [US3] Implement `BackupEngine.restore_logical(instance, containers, *, overwrite=False, assume_yes=False)` in `src/pglite_pydb/backup.py` per data-model.md §9c: accept `["--latest"]` sentinel → call `BackupLocation.list_logical_containers(...)[-1]`; validate each container's filename prefix + manifest kind + schema_version; collect union of schemas to be restored; under `InstanceLock`, start target TCP server; detect conflicting pre-existing schemas → branch on `overwrite`; for each container, run `psql -f <schema>.sql` inside an explicit `BEGIN; ... COMMIT;` so on mid-container failure the target ROLLBACK keeps it consistent (FR-027). Make T043–T051 pass.
- [X] T060 [US3] Implement `BackupEngine.restore_full_snapshot(instance, container, *, assume_yes=False, assume_yes_destroy=False)` in `src/pglite_pydb/backup.py` per data-model.md §9d: validate filename prefix; show confirmation #1 (selected container details); call `is_completely_empty_for_full_snapshot_restore(target)` (from T013); if NOT completely empty, require confirmation #2 via `_confirm_destroy` (from T012); acquire `InstanceLock`; stash the target's existing `.pglite-pydb/` subtree in-memory (bytes); extract tar over the target dir; restore the stashed sidecar (or leave absent if none existed); on mid-extraction failure write a sentinel `<data-dir>/.pglite-pydb/FAILED_RESTORE` file so future wrapper starts refuse the instance. Make T052–T058 pass.
- [X] T061 [US3] Wire `pglite-pydb restore` subcommand in `src/pglite_pydb/cli/main.py` per contracts/cli.md: mode dispatch (logical vs `--full-snapshot`); container-path resolution (bare filename → relative to configured backup location); **on start, print the same two-line "instance data dir / backup location" stderr banner used by `backup` in T040 (FR-011 — the configured backup location MUST be reported to the operator on both `backup` and `restore --latest`)**; exit-code mapping identical to T040. Make T042 pass and close any residual gaps in T043–T058.
- [X] T062 [US3] Make manager.py honour the `<data-dir>/.pglite-pydb/FAILED_RESTORE` sentinel from T060: on `start()`, if present, raise `InvalidDataDirError` with an actionable message instructing the operator to remove the sentinel (and with a short explanation). Add a dedicated test case in `tests/test_restore_full_snapshot.py::test_failed_restore_sentinel_blocks_future_start`.

**Checkpoint**: US3 shippable on top of US1+US2. Every Acceptance Scenario in spec.md US3 plus every full-snapshot-restore requirement (FR-034..FR-036) maps to a passing test. SC-004 restore-side, SC-006 (all failure modes named), and SC-009 (`--latest` always deterministic + confirmed) all pass.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Cross-platform parity, end-to-end validation via the quickstart, the CALVER bump, and the docs-pointer update. These touch multiple stories and must come last.

- [ ] T063a CI matrix gate for FR-029 — add (or update) `.github/workflows/test.yml` so the full `pytest` suite runs on both an `ubuntu-latest` and a `windows-latest` runner with Node 20, `pg_dump`/`psql` client tools installed, and `pglite-pydb` editable-installed. The job matrix MUST fail the PR if either OS's suite fails. This operationalises FR-029's "Tests MUST pass on both Linux and Windows/PowerShell" at CI time rather than relying on manual dual runs. If no workflow file exists yet (check `.github/workflows/`), create one; if one exists from feature 001, extend its matrix to cover this feature's new test files. Maps FR-029.
- [ ] T063 [P] Cross-platform portability test `tests/test_cross_platform_portability.py` — two pytest variants, one marked `windows_only` and the inverse marked `@skipif(IS_WINDOWS)`: (a) produce a logical container on this host, (b) unpack + reinspect the manifest + assert every entry name round-trips under PAX/UTF-8, (c) if a cross-platform CI artifact cache is available, also run the inverse (restore on Linux of a Windows-produced artifact and vice versa). Maps SC-005.
- [ ] T064 [P] End-to-end quickstart runner `tests/test_quickstart_runbook.py` — executes the 10 steps of `specs/003-pglite-path-backup-restore/quickstart.md` in sequence against a tmp_path + tmp backup location, asserting every documented stdout/stderr shape. Guards SC-007 (unfamiliar-operator 10-minute run) empirically.
- [ ] T065 [P] Update `src/pglite_pydb/__init__.py` to re-export `BackupEngine` and the error classes for programmatic users; add `__all__` entry.
- [ ] T066 CALVER version bump in `pyproject.toml`: `2026.4.21.1` → `2026.4.21.2` per memory's CALVER scheme (SameDay build+1) and plan line 96. NO zero-day; `.21.` remains. Also update `pglite_pydb_version` sample values in tests that assert on the manifest string. Only run on the actual ship day; if ship day has slipped, bump to `2026.M.D.1` where `M`/`D` are today's real month/day.
- [ ] T067 Update `CLAUDE.md` top-level `<!-- SPECKIT -->` block so future Claude invocations know feature 003 is complete: change "Active feature" to the next feature or to `(none — 003 landed YYYY-MM-DD)`; retain the artefact pointers as historical reference under a "Previously shipped" heading. Plan line 122 foreshadows this update.
- [ ] T068 Manual SC-007 dry-run: fresh operator (or a fresh shell with `PATH` cleaned to simulate one) follows `quickstart.md` end-to-end using only `pglite-pydb --help` + the runbook text; clock the run; file a follow-up if it exceeds 10 minutes. Not automatable — record the result in a short comment on the feature's completion commit.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No deps — start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1. **BLOCKS all user stories.**
- **Phase 3 (US1)**: Depends on Phase 2. No dependency on Phase 4 or Phase 5.
- **Phase 4 (US2)**: Depends on Phase 2. Does not strictly depend on Phase 3's *tests* passing, but shares `manager.py` changes from T014 — in practice do US1 first or run US1+US2 by two people who coordinate on `manager.py`.
- **Phase 5 (US3)**: Depends on Phase 2. Needs containers to restore *at test time*, so **US3 integration tests are run against fixtures produced by US2's `BackupEngine.create_logical` / `create_full_snapshot`** — US3 implementation can start in parallel with US2 (different functions in the same `backup.py`) but US3 tests become green only once US2 implementation is complete.
- **Phase 6 (Polish)**: Depends on all desired user stories being complete.

### User Story Dependencies

- **US1 (P1)** — none; this is MVP and self-sufficient.
- **US2 (P2)** — depends on US1's mandatory-path contract (`backup` needs a resolvable target) but does not import US1 code beyond `PGliteConfig` + `InstanceLock` landed in Phase 2.
- **US3 (P3)** — depends on US2's container format (restore is the consumer of US2's artefacts). Cross-story dependence is confined to `backup.py` and the on-disk container shape, both of which are frozen by the manifest + CLI contracts.

### Within Each User Story

- Tests in the "Tests for User Story N" section MUST be written first and MUST FAIL before implementation begins (FR-029 + plan testing norms).
- Models / dataclasses before engines; engines before CLI wiring.
- Core implementation before cross-cutting integration.

### Parallel Opportunities

- **Phase 1**: T003, T004, T005 can run in parallel (different files).
- **Phase 2**: T006+T009+T011 (test authors on three separate files) run in parallel; T008 and T013 are `[P]` and touch no file overlapping with the test authors; T007/T010/T012/T014/T015 must serialise within their dependency chain (T010 depends on T008's errors; T014 depends on T007+T010+T012+T013).
- **Phase 3**: T016–T021 all `[P]` (six independent test files-or-functions); T022 serialises after their failures are diagnosed.
- **Phase 4**: T023–T035 all `[P]`; T036..T041 serialise on `backup.py`, `cli/main.py` but T036 (config subcommand) and T037 (timestamp util) can run before T038/T039 which can run in parallel since they operate on different methods of the same class — coordinate editor-lock on `backup.py`.
- **Phase 5**: T042–T058 all `[P]`; T059/T060 touch disjoint methods in `backup.py` and can run in parallel; T061 serialises after both.
- **Phase 6**: T063/T064/T065 all `[P]`; T066/T067 serialise on `pyproject.toml` / `CLAUDE.md`; T068 is manual.

---

## Parallel Example: User Story 2 (after Phase 2 is green)

```bash
# Launch all US2 test authors together (six independent test files):
Task: "CLI contract test pglite-pydb backup argument grammar in tests/test_cli_backup_args.py"                      # T023
Task: "Integration test single-schema logical backup in tests/test_backup_logical.py::test_single_schema"           # T024
Task: "Integration test list-of-schemas logical backup in tests/test_backup_logical.py::test_list_of_schemas"       # T025
Task: "Integration test --all mode in tests/test_backup_logical.py::test_all_mode"                                  # T026
Task: "Integration test --all on empty instance in tests/test_backup_logical.py::test_all_on_schemaless_..."         # T027
Task: "Integration test missing-schema-fails-with-no-partial in tests/test_backup_logical.py::test_missing_schema"  # T028
Task: "Integration test no-backup-location-configured in tests/test_backup_logical.py::test_no_backup_location..."   # T029
Task: "Integration test unwritable location in tests/test_backup_logical.py::test_unwritable_location_fails"         # T030
Task: "Integration test 10x rapid-fire distinct in tests/test_backup_logical.py::test_rapid_fire_10x_unique"         # T031
Task: "Integration test lock required in tests/test_backup_logical.py::test_default_requires_exclusive_lock"         # T032
Task: "Integration test full-snapshot layout in tests/test_backup_full_snapshot.py::test_full_snapshot_layout..."    # T033
Task: "Integration test full-snapshot requires lock in tests/test_backup_full_snapshot.py::test_..._no_force_hot"    # T034
Task: "CLI contract test config subcommand in tests/test_cli_config.py"                                              # T035
```

Once all thirteen tests are in and failing, implementation tasks T036 → T041 land serially (most share `backup.py` or `cli/main.py`).

---

## Implementation Strategy

### MVP First (US1 only)

1. Complete Phase 1 (Setup) — T001..T005.
2. Complete Phase 2 (Foundational) — T006..T015. This is the biggest serial stretch in the plan because every story depends on it.
3. Complete Phase 3 (US1) — T016..T022.
4. **STOP & VALIDATE**: `pytest tests/test_data_dir_mandatory.py tests/test_instance_lock.py tests/test_sidecar_config.py` green on Linux + Windows CI. This alone is a shippable increment: feature 001 users get mandatory-path + lock semantics without any backup tooling.

### Incremental Delivery

1. Ship MVP (US1) → internal dogfood.
2. Add US2 (T023..T041) → integration tests exercise real `pg_dump`; CI matrix includes a job with PostgreSQL 16 client tools installed. Ship as a 2nd increment — operators can now back up but not restore (useful for archival / fixture capture even without restore).
3. Add US3 (T042..T062) → closes the loop; ship the full feature.
4. Phase 6 (T063..T068) → final polish + version bump + docs pointer.

### Parallel Team Strategy (3 developers)

- Dev A: Phase 1 → Phase 2 (land foundation solo, since it's the serial spine).
- Once T015 merges:
  - Dev A: US1 (T016..T022).
  - Dev B: US2 test authors (T023..T035) in parallel.
  - Dev C: US3 test authors (T042..T058) in parallel (tests can be written against the contract even before US2 impl lands).
- After US1 merges, Dev A joins US2 implementation (T036..T041) with Dev B.
- After US2 impl merges, Dev A+C land US3 implementation (T059..T062).
- Dev B handles Phase 6.

---

## Validation Checklist (format self-check)

Every task above satisfies all five format requirements:

1. ✅ Starts with `- [ ]` markdown checkbox.
2. ✅ Sequential ID `T001..T068`.
3. ✅ `[P]` only on tasks that touch files no other incomplete task touches.
4. ✅ `[US1]` / `[US2]` / `[US3]` label present exactly on tasks inside Phase 3 / 4 / 5; absent on Phase 1 / 2 / 6 tasks.
5. ✅ Every task names a concrete file path (or set of file paths) under `D:\bstdev\pglite\`.

---

## Summary

- **Total tasks**: 69 (T001..T068 plus T063a).
- **Phase 1 (Setup)**: 5 tasks (T001..T005).
- **Phase 2 (Foundational)**: 10 tasks (T006..T015).
- **Phase 3 (US1)**: 7 tasks (T016..T022) — 6 tests + 1 gap-closer.
- **Phase 4 (US2)**: 19 tasks (T023..T041) — 13 tests + 6 implementation.
- **Phase 5 (US3)**: 21 tasks (T042..T062) — 17 tests + 4 implementation.
- **Phase 6 (Polish)**: 7 tasks (T063, T063a, T064..T068).
- **Parallel opportunities**: 37 `[P]`-marked tasks (most of the test authors).
- **Suggested MVP**: Phase 1 + Phase 2 + Phase 3 (US1) = 22 tasks, delivers mandatory-path + locking + sidecar without any backup tooling.
- **Independent-test criteria** are stated at the top of each user-story phase and map 1:1 to spec.md's US1/US2/US3 "Independent Test" narratives.
