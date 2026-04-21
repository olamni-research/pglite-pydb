# Feature Specification: Mandatory Data Path + Backup/Restore Commands

**Feature Branch**: `003-pglite-path-backup-restore`
**Created**: 2026-04-21
**Status**: Draft

## Clarifications

### Session 2026-04-21

- Q: What should a backup container be (format and contents)? → A: Single archive file (e.g. `.tar.gz`) per backup, containing one logical SQL dump file per included schema plus a `manifest.json` (timestamp, source instance path, included schemas).
- Q: Where is each instance's configured backup location persisted? → A: Sidecar config file inside the instance's data directory (e.g. `<data-dir>/.pglite-pydb/config.json`); no central registry.
- Q: Policy for backing up a running instance? → A: By default `backup` requires exclusive access to the data directory (acquires the same FR-006 lock) and fails fast with a clear "instance in use" error; a `--force-hot` flag opts into a best-effort hot backup against a running instance.
- Q: Symlink policy for the data-directory path? → A: Always follow symlinks; the fully-resolved real path is the canonical instance identity used for logging, locking, and the sidecar config location.
- Q: Restore overwrite opt-in granularity? → A: Single `--overwrite` flag covers all conflicts in the invocation, but `restore` MUST first list every schema it would replace and require interactive confirmation (same TTY / `--assume-yes` rules as FR-022).
- Q: Add full-snapshot backup/restore mode? → A: Yes — a `--full-snapshot` mode on `backup` produces a timestamped physical (file-level) snapshot of the entire data directory, named with prefix `FULL_SNAPSHOT_`. A matching `restore --full-snapshot` requires explicit operator confirmation, and an additional second confirmation when the target data directory is not completely empty.
- Q: How does `--latest` interact with the two container kinds (logical vs `FULL_SNAPSHOT_*`)? → A: Each `--latest` is scoped to its own kind: `restore --latest` (logical) ignores `FULL_SNAPSHOT_*` containers, and `restore --full-snapshot --latest` (added by this clarification) ignores logical containers. The two restore paths never cross-select.
- Q: What counts as a "completely empty" target directory for the FR-035 second confirmation? → A: Empty = directory does not exist OR contains no PGlite data files of any kind. The wrapper's own sidecar `.pglite-pydb/` and OS metadata files (`.DS_Store`, `Thumbs.db`, `desktop.ini`) are ignored when evaluating emptiness; everything else triggers the second confirmation.
- Q: How should the sidecar `.pglite-pydb/config.json` be handled in full-snapshot backup/restore? → A: Exclude it from the snapshot archive entirely. On restore, the target's existing sidecar is preserved as-is; if the target has none, none is created and the operator must reconfigure the backup location before the next `backup`.

**Input**: User description: "add a mandatory path argument to the pglite-pydb pglite wrapper that is passed to the pglite wasm process when it is started to ensure the db instance is created or started in the correct place. test that pglite-pydb can reliably start in the path and pglite does not start elsewhere. test that starting pglite in the same path does not overwrite but only start with the existing db. Create pglite-pydb command backup for backing up one specific db (schema) or a list of schemas or all dbs in an instance. Create a restore pglite-pydb command for restoring a database, or list of databases from backups"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Explicit Data Directory for PGlite Instance (Priority: P1)

A developer or operator starting a PGlite instance through `pglite-pydb` wants to guarantee that the underlying PGlite WASM process persists its data to an explicit, known location on disk. They must supply a data directory path when creating or starting the wrapper; if no path is supplied, startup fails with a clear error. When the same path is reused, the existing data is opened without being overwritten or reinitialized.

**Why this priority**: Data location is the foundation for every other capability (backup, restore, test isolation, reproducible environments). Silent defaults or wrong directories risk data loss, cross-test contamination, and unreliable backups. This story must land first because backup/restore depend on knowing exactly where an instance's data lives.

**Independent Test**: Start `pglite-pydb` with an explicit path, create a table and insert rows, stop the instance, restart with the same path, and confirm the table and rows are still present. Separately, omit the path and confirm startup fails with a clear error. Inspect the filesystem to verify no PGlite data files are created outside the supplied path.

**Acceptance Scenarios**:

1. **Given** a user invokes the PGlite wrapper without a data directory path, **When** they attempt to start an instance, **Then** startup fails immediately with an error message that names the required path argument and no PGlite process is launched.
2. **Given** a user supplies a path to a directory that does not yet exist, **When** they start an instance, **Then** the directory is created and a new PGlite database is initialized inside it, and only inside it.
3. **Given** a user supplies a path to a directory that already contains a PGlite database previously created by the wrapper, **When** they start an instance, **Then** the existing database is opened and its data (schemas, tables, rows) is preserved with no overwrite, reinitialization, or silent data loss.
4. **Given** two instances are started concurrently with two different paths, **When** each writes data, **Then** each instance persists only to its own path and no data files appear outside either path.
5. **Given** a user supplies a path that points to a file or to a directory containing unrelated non-PGlite content, **When** they start an instance, **Then** startup fails with a clear error that distinguishes "not a directory" from "directory is not empty / not a valid PGlite data directory" and does not modify the target location.

---

### User Story 2 - Backup One, Some, or All Databases/Schemas (Priority: P2)

An operator wants to capture a point-in-time backup of data from a running or stopped PGlite instance at a known data directory. They can back up a single schema, a named list of schemas, or everything in the instance. Each PGlite instance has a configured backup location (set once and persisted with the instance); when the operator runs `backup`, the command creates a new timestamp-named container (folder or archive) inside that configured location, so multiple backups accumulate over time without overwriting each other and are discoverable by timestamp.

**Why this priority**: Once data location is deterministic (Story 1), the next highest-value capability is protecting that data. Backups enable safe upgrades, test fixtures, migration rehearsals, and disaster recovery. It precedes restore because a restore command is meaningless without backup artifacts to operate on. A per-instance backup location removes the need for the operator to remember destination paths between invocations.

**Independent Test**: Configure a backup location for an instance, populate two schemas with known rows, run the backup command in three modes (single schema, list of schemas, all) at different times, and verify that each invocation produces a new timestamp-named container inside the configured location. Confirm each container records which schema(s) and instance path it originated from, and that no earlier backup is overwritten.

**Acceptance Scenarios**:

1. **Given** a PGlite instance whose backup location has been configured to a known directory, **When** the operator runs `backup` for schema `app`, **Then** a new timestamp-named container is created inside the configured backup location that contains only `app` data and is labeled as such, and any previously existing backups in that location are untouched.
2. **Given** the same instance, **When** the operator runs `backup` with a list of schemas `[app, analytics]`, **Then** a new timestamp-named container is produced inside the configured backup location containing both schemas.
3. **Given** the same instance, **When** the operator runs `backup` in "all" mode, **Then** a new timestamp-named container is produced inside the configured backup location containing every user schema/database in the instance.
4. **Given** an instance for which no backup location has yet been configured, **When** the operator runs `backup`, **Then** the command fails with a clear error instructing the operator to configure a backup location for this instance first (or to supply one explicitly), and no artifact is produced.
5. **Given** the user requests backup of a schema that does not exist, **When** the command runs, **Then** it fails with a clear error naming the missing schema and produces no partial artifact.
6. **Given** the configured backup location is not writable, **When** the command runs, **Then** it fails with a clear error identifying the location and does not corrupt or delete existing files there.
7. **Given** two `backup` invocations run against the same instance within the same second or rapidly in succession, **When** both complete, **Then** each produces a distinct container (timestamps are sufficiently granular, or disambiguated by suffix) and neither overwrites the other.
8. **Given** a backup completes successfully, **When** the user inspects the container, **Then** it records (at minimum) creation timestamp, source instance path, and the list of schemas/databases included, so a later restore can be validated against its intended target.

---

### User Story 3 - Restore One or More Databases from Backups (Priority: P3)

An operator wants to reconstitute data into a PGlite instance from one or more previously produced backup containers. They can restore a single database/schema or a list of them. The restore command operates against an explicit instance data path (from Story 1). The operator either names specific backup container(s) to restore from, or passes a `--latest` flag to pick the most recent backup from the instance's configured backup location; in the `--latest` case the command shows the operator which backup it selected (timestamp and included schemas) and requires an interactive confirmation before proceeding. The operator also controls whether existing objects in the target are replaced or preserved.

**Why this priority**: Restore completes the backup/restore loop and is the consumer of Story 2's artifacts. It is P3 because without Story 1 the target location is ambiguous and without Story 2 there is nothing to restore; once both exist, restore is the final piece that makes backups actually useful. Naming a backup or explicitly opting into "latest" (with confirmation) prevents the single most dangerous class of restore mistakes: quietly restoring the wrong point-in-time over live data.

**Independent Test**: Take the timestamp-named backup containers produced in Story 2 across at least two different timestamps, recreate a target instance at a new path, and (a) run restore naming a specific older backup and verify its exact contents are restored; (b) run restore with `--latest` and verify the command prompts with the most recent backup's details and only proceeds on confirmation; (c) run restore with neither a name nor `--latest` and verify the command fails with a clear error.

**Acceptance Scenarios**:

1. **Given** a specific named backup container for schema `app` and an empty target instance at a known data path, **When** the user runs restore naming that container and the target path, **Then** schema `app` and all its data exist in the target instance and no other schemas are altered.
2. **Given** a list of named backup containers, **When** the user runs restore with the list against a target instance path, **Then** each backup's schemas/databases are applied to the target and the final state contains all of them.
3. **Given** an instance with a configured backup location containing multiple timestamped backups, **When** the user runs restore with `--latest` and no specific backup name, **Then** the command identifies the most recent backup by timestamp, displays its timestamp and included schemas/databases to the user, asks for confirmation, and only proceeds when the user confirms.
4. **Given** the same `--latest` invocation, **When** the user declines the confirmation prompt, **Then** the command exits without making any changes to the target instance.
5. **Given** the user invokes restore without naming a specific backup and without `--latest`, **When** the command runs, **Then** it fails with a clear error instructing the operator to either name a backup container or pass `--latest`, and performs no work.
6. **Given** `--latest` is passed but the configured backup location contains zero backups (or no backup location is configured), **When** the command runs, **Then** it fails with a clear error distinguishing "no backups found" from "no backup location configured" and performs no work.
7. **Given** a target instance that already contains schema `app` with different data, **When** the user runs restore for a backup of `app` without explicit overwrite permission, **Then** the command fails with a clear conflict error and leaves the existing data unchanged.
8. **Given** the same target instance, **When** the user runs restore for a backup of `app` with explicit overwrite permission, **Then** the existing `app` schema is replaced with the backup's contents and other schemas are left untouched.
9. **Given** a backup container that is corrupt, truncated, or not recognizable as a pglite-pydb backup, **When** the user runs restore, **Then** the command fails with a clear error identifying which container was rejected and makes no changes to the target instance.
10. **Given** the target instance path does not exist or was not supplied, **When** restore is invoked, **Then** it fails with the same "mandatory path" error contract as Story 1 and performs no work.

---

### Edge Cases

- User supplies a **relative path** that resolves differently depending on the caller's working directory — the instance MUST anchor to a single resolved absolute path and log/report that resolved path so operators can verify location.
- User supplies a path containing **spaces, non-ASCII characters, or Windows drive letters** (e.g., `D:\bstdev\pglite\data`) — must be handled correctly on both Linux and Windows/PowerShell.
- User supplies a **symlinked directory** — the wrapper always follows symlinks and uses the fully-resolved real path as the canonical instance identity (per FR-003); two different symlinks pointing at the same real directory therefore resolve to the same instance and contend for the same FR-006 lock.
- Two processes try to start PGlite against the **same data path simultaneously** — the second start must fail fast with a clear "already in use / locked" error rather than corrupt the database.
- Backup is invoked while the instance is under active write load — the artifact must be internally consistent (no torn writes) or the command must fail clearly; partial/corrupt artifacts are not acceptable.
- Restore is invoked into an instance that is **currently running and serving connections** — the command must either safely coordinate with the running instance or refuse with a clear error, never silently corrupt in-flight transactions.
- Backup/restore artifacts are moved **between Linux and Windows hosts** — artifacts must remain portable and restore correctly regardless of the host that produced them.
- User requests "all" backup on an instance with **zero user schemas** — produce a valid empty-but-well-formed artifact rather than erroring, so scripted workflows don't break on fresh instances.
- Backup destination or restore source path is supplied as a **directory vs. a file** — behavior must be unambiguous (e.g., one artifact per schema vs. one combined artifact) and documented.
- The instance's **configured backup location is missing, deleted, or on a disconnected drive** at the moment `backup` or `restore --latest` runs — the command must fail with a clear error that names the configured location, not create a new one silently.
- Two `backup` invocations are started at the **same wall-clock second** — naming must still yield distinct containers (e.g., sub-second precision or a numeric suffix) so neither clobbers the other and "latest" remains well-defined.
- System clock is **skewed or moved backwards** between backups, so a newer backup has an earlier timestamp than an older one — "latest" selection must be deterministic and documented (e.g., picks the lexically-highest timestamp name, or the most recently written container) and the chosen rule must be surfaced to the user during `--latest` confirmation.
- Operator runs `restore --latest` non-interactively (e.g., CI pipeline with no TTY) — the command must not silently auto-confirm; it must either require an explicit auxiliary "assume yes" flag or fail with a clear error stating that confirmation cannot be obtained.
- Operator changes the **configured backup location** for an instance while older backups still exist at the previous location — the command must define and document whether "latest" searches only the current location (and the old backups become invisible until moved) or both; behavior must be predictable.

## Requirements *(mandatory)*

### Functional Requirements

#### Mandatory data-directory path

- **FR-001**: The `pglite-pydb` PGlite wrapper MUST require a data directory path as a mandatory argument every time a PGlite instance is started or created; omitting the path MUST cause startup to fail before any PGlite process is launched.
- **FR-002**: The wrapper MUST pass the supplied data directory path to the underlying PGlite WASM process so that PGlite initializes and persists all database files exclusively within that directory.
- **FR-003**: The wrapper MUST resolve the supplied path to an unambiguous absolute location, **fully following any symlinks** (i.e., to the canonical real path), and MUST report that resolved real path to the caller (e.g., in logs or a return value) so operators can verify where data will live. The resolved real path is the canonical instance identity used for the FR-006 lock and for locating the sidecar config (FR-008).
- **FR-004**: If the supplied directory does not exist, the wrapper MUST create it and initialize a new PGlite database inside it. If the directory already contains a PGlite database previously created by the wrapper, the wrapper MUST open the existing database without reinitializing, overwriting, or otherwise destroying existing data.
- **FR-005**: The wrapper MUST reject, with a clear actionable error, any path that is not usable as a PGlite data directory (e.g., points to a regular file, is not writable, or contains content that is not a recognizable PGlite data layout and is not empty), and MUST make no modifications to the target location in that case.
- **FR-006**: The wrapper MUST prevent two instances from simultaneously using the same data directory on the same host, failing fast with a clear "path in use / locked" error rather than risking corruption.
- **FR-007**: The wrapper MUST behave identically on Linux and Windows/PowerShell with respect to mandatory-path semantics, including handling of absolute paths, relative paths, spaces, and drive letters.

#### Per-instance backup location configuration

- **FR-008**: `pglite-pydb` MUST allow each PGlite instance (identified by its data directory path per FR-001) to have a persistently configured **backup location** — a directory on the local filesystem where `backup` writes containers and where `restore --latest` searches for them. The configuration MUST be persisted in a sidecar config file inside the instance's own data directory (under `<data-dir>/.pglite-pydb/config.json`); no central or per-user registry is used, so the instance remains self-contained and portable when its data directory is moved.
- **FR-009**: The configured backup location MUST survive instance restarts (it is associated with the instance, not with a single command invocation) and MUST be inspectable and changeable by the operator through `pglite-pydb` commands without hand-editing the sidecar config file.
- **FR-010**: Before `backup` or `restore --latest` will operate, a backup location MUST be configured for the target instance; if one is not configured, the command MUST fail with a clear actionable error telling the operator how to configure it.
- **FR-011**: The wrapper MUST resolve the configured backup location to an unambiguous absolute path and MUST report it to the operator when running `backup` or `restore --latest`, so the operator can verify where backups go and come from.

#### Backup command

- **FR-012**: `pglite-pydb` MUST provide a `backup` command that targets a PGlite instance identified by its data directory path (per FR-001) and writes each new backup into the instance's configured backup location (per FR-008).
- **FR-013**: The `backup` command MUST support three selection modes: (a) a single schema/database, (b) an explicit list of schemas/databases, and (c) all user schemas/databases in the instance.
- **FR-014**: Each `backup` invocation MUST produce a **new timestamp-named container** inside the configured backup location. A container is a single archive file (`.tar.gz`) whose internal layout contains exactly one logical SQL dump file per included schema and a `manifest.json` describing the backup. The container's filename MUST encode the creation timestamp in a format that sorts chronologically and is human-readable, and MUST be unique even when multiple backups are created in rapid succession (e.g., sub-second precision or a disambiguating suffix). Existing containers in the location MUST NOT be modified or deleted.
- **FR-015**: The `backup` command MUST fail with a clear error and produce no partial container if any requested schema/database does not exist in the source instance.
- **FR-016**: Each backup container MUST be self-describing: the embedded `manifest.json` MUST record at minimum the creation timestamp, the source instance's resolved data directory path, and the list of schemas/databases contained, so that restore operations can validate intent.
- **FR-017**: The `backup` command MUST produce containers that are internally consistent (no torn or partial writes). By default it MUST acquire exclusive access to the target instance's data directory (the same lock as FR-006) for the duration of the dump; if another process holds the lock, `backup` MUST fail fast with a clear "instance in use" error and produce no container. A `--force-hot` flag MAY be supplied to opt into a best-effort backup against a running instance, in which case the command MUST still produce only an internally-consistent artifact (relying on the SQL dump's transactional snapshot) or fail clearly. In all failure cases the command MUST clean up any half-written container so the configured backup location is not polluted.
- **FR-018**: Backup containers MUST be portable between Linux and Windows hosts running the same or compatible `pglite-pydb` versions.

#### Restore command

- **FR-019**: `pglite-pydb` MUST provide a `restore` command that targets an instance identified by its mandatory data directory path (per FR-001) and accepts either (a) one or more named backup containers to restore from, or (b) a `--latest` flag that selects the most recent backup from the instance's configured backup location.
- **FR-020**: If `restore` is invoked without naming a specific backup and without `--latest`, it MUST fail with a clear error instructing the operator to name a backup container or pass `--latest`.
- **FR-021**: When `--latest` is used, the `restore` command MUST identify the most recent backup in the configured backup location using a documented, deterministic rule (e.g., the lexically-highest timestamp-encoded name), display to the operator at minimum the selected container's name, timestamp, and included schemas/databases, and require an explicit interactive confirmation before applying the restore. Declining the prompt MUST abort the operation with no changes. `--latest` selection is **scoped to the restore mode's container kind**: `restore --latest` (logical mode) MUST consider only logical containers and MUST ignore any `FULL_SNAPSHOT_*` files in the location; `restore --full-snapshot --latest` MUST consider only `FULL_SNAPSHOT_*` containers and MUST ignore logical ones.
- **FR-022**: When `--latest` is used in a non-interactive context (no TTY), the `restore` command MUST NOT auto-confirm; it MUST either require an explicit auxiliary "assume-yes" flag or fail with a clear error stating that confirmation cannot be obtained.
- **FR-023**: When `--latest` is used but the configured backup location contains no recognizable backups, or no backup location is configured, the command MUST fail with a clear error that distinguishes these two conditions and makes no changes.
- **FR-024**: The `restore` command MUST support restoring (a) a single database/schema from a single named container and (b) a list of databases/schemas from one or more named containers in a single invocation.
- **FR-025**: The `restore` command MUST, by default, refuse to clobber a schema/database that already exists in the target instance, failing with a clear conflict error that names the conflicting objects. Overwrite MUST require an explicit opt-in via a single `--overwrite` flag covering all conflicts in the invocation. When `--overwrite` is supplied, the command MUST first list every schema/database it would replace and require interactive confirmation before proceeding, applying the same TTY / `--assume-yes` rules as FR-022 (no silent auto-confirm in non-interactive contexts). When confirmed, only the conflicting objects covered by the listed backups MUST be replaced; all other schemas in the target instance MUST be left untouched.
- **FR-026**: The `restore` command MUST reject containers that are corrupt, truncated, or not recognizable as `pglite-pydb` backups, naming the rejected container and making no changes to the target instance.
- **FR-027**: The `restore` command MUST leave the target instance in a consistent state: on failure at any point, no partially-restored objects remain visible to clients.
- **FR-028**: The `restore` command MUST fail with the same "mandatory path" error contract as FR-001 if the target data directory path is missing.

#### Full-snapshot backup and restore

- **FR-031**: `backup` MUST support a `--full-snapshot` mode that produces a **physical**, file-level snapshot of the entire PGlite data directory (every file PGlite persists, not a logical SQL dump). Schema-level selection (FR-013) does not apply in this mode: a full snapshot is always whole-instance.
- **FR-032**: A full snapshot MUST be written into the instance's configured backup location (per FR-008) as a single archive file whose name is prefixed with `FULL_SNAPSHOT_` followed by the same chronologically-sortable timestamp format used by logical backups (e.g., `FULL_SNAPSHOT_YYYYMMDD-HHMMSS[.fff].tar.gz`). The archive MUST contain the data-directory file tree **excluding the wrapper's own sidecar `.pglite-pydb/` directory** (which is per-instance configuration, not data, and would otherwise carry the source instance's backup-location pointer to any restore target), plus a `manifest.json` recording at minimum the creation timestamp, the source instance's resolved data directory path, and a `kind: "full-snapshot"` marker so restore can distinguish it from logical backups. Existing containers in the location MUST NOT be modified.
- **FR-033**: To guarantee on-disk consistency, `--full-snapshot` MUST acquire the FR-006 exclusive data-directory lock for the duration of the snapshot. There is no `--force-hot` equivalent for full snapshots; if the lock cannot be obtained, the command MUST fail fast with a clear "instance in use" error and produce no container.
- **FR-034**: `restore` MUST support a `--full-snapshot <container>` mode that restores a previously-produced `FULL_SNAPSHOT_*` container by replacing the contents of the target instance's data directory with the snapshot's file tree. This mode MUST refuse to operate on logical backups, and `restore` (without `--full-snapshot`) MUST refuse to operate on `FULL_SNAPSHOT_*` containers; the wrong combination MUST fail with a clear error naming the container kind. `restore --full-snapshot` MUST also accept `--latest` in place of an explicit container name; in that case it selects the most recent `FULL_SNAPSHOT_*` container in the configured backup location using the same deterministic rule as FR-021 and ignores logical containers entirely.
- **FR-035**: `restore --full-snapshot` MUST require **explicit operator approval** before applying any changes: it MUST display the selected container's name, timestamp, and source instance path, and require an interactive confirmation. If the target data directory is **not completely empty**, the command MUST require a **second, distinct confirmation** that explicitly acknowledges the existing contents will be destroyed and replaced. For this purpose, the target data directory is considered "completely empty" if it does not exist, OR it contains no PGlite data files of any kind; the wrapper's own sidecar `.pglite-pydb/` directory and OS metadata files (`.DS_Store`, `Thumbs.db`, `desktop.ini`) MUST be ignored when evaluating emptiness. Any other content (including any recognizable PGlite data file or any unrecognized non-trivial file/directory) MUST trigger the second confirmation. Both confirmations MUST follow the same TTY / `--assume-yes` rules as FR-022 (no silent auto-confirm in non-interactive contexts; the non-empty-target second confirmation MUST require its own opt-in flag, separate from the first, when running non-interactively).
- **FR-036**: `restore --full-snapshot` MUST acquire the FR-006 exclusive lock on the target data directory for the duration of the restore. On failure at any point, the target directory MUST be left in a consistent state — either the original pre-restore contents (if no destructive write has begun) or a clearly-marked failed state that prevents the wrapper from later treating it as a valid PGlite instance — never a silently-corrupted half-restored mix. The target's existing sidecar `.pglite-pydb/` directory (if present) MUST be preserved across the restore; if the target has no sidecar, none is created, and the operator MUST reconfigure the backup location via `pglite-pydb` before the next `backup` against the restored instance.

#### Cross-cutting

- **FR-029**: All capabilities in this feature MUST have automated tests that verify: (i) startup fails without a path; (ii) startup in a fresh path creates data only inside that path and nowhere else on the filesystem; (iii) startup in a pre-existing path preserves existing data without overwrite; (iv) a backup location can be configured, persisted, and read back for an instance; (v) `backup` in each of the three selection modes produces a new timestamp-named container in the configured location without overwriting earlier containers; (vi) `restore` with a named container, `restore --latest` with confirmation, and `restore` with no selector each behave per FR-019–FR-023; (vii) restore from produced containers reproduces the original schemas/rows in a target instance. Tests MUST pass on both Linux and Windows/PowerShell.
- **FR-030**: Error messages for all failure modes above MUST be actionable: they MUST identify the argument, path, schema, container, or configuration at fault, and MUST NOT require the user to read source code to diagnose.

### Key Entities

- **PGlite Instance**: A running or stopped PGlite database process managed by the `pglite-pydb` wrapper. Uniquely identified on a host by its resolved absolute data directory path. Contains zero or more user schemas/databases. Carries an associated Backup Location configuration.
- **Data Directory**: An absolute filesystem location that holds all persistent files for exactly one PGlite Instance. Mandatory input to start/create an instance; also the target of restore and source of backup.
- **Backup Location**: A persistently configured absolute directory path associated with a PGlite Instance, under which `backup` writes new containers and from which `restore --latest` selects the most recent one. Exactly one per instance. Stored in a sidecar config file inside the instance's data directory (`<data-dir>/.pglite-pydb/config.json`).
- **Schema/Database Selection**: The set of schemas or databases a `backup` or `restore` command operates on. May be a single name, an explicit list, or the sentinel "all".
- **Backup Container**: A self-describing single-file archive (`.tar.gz`) produced by `backup` inside the instance's configured Backup Location. Named by a chronologically-sortable timestamp. Comes in two kinds, distinguished by filename prefix and by a `kind` field in the embedded `manifest.json`:
  - **Logical** (default): contains one SQL dump file per included schema plus `manifest.json` (`kind: "logical"`, timestamp, source instance path, included schemas). Used by the standard `backup` selection modes and by `restore` / `restore --latest`.
  - **Full snapshot** (produced by `backup --full-snapshot`): filename prefixed with `FULL_SNAPSHOT_`; contains a physical copy of the data-directory file tree **excluding the wrapper's sidecar `.pglite-pydb/`**, plus `manifest.json` (`kind: "full-snapshot"`, timestamp, source instance path). Restored only via `restore --full-snapshot`; on restore the target's existing sidecar (if any) is preserved and not replaced.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of wrapper-start attempts without a data directory path fail before any PGlite process is launched, verified by automated tests on both Linux and Windows.
- **SC-002**: After starting the wrapper with a given path, 0 PGlite data files exist outside that resolved path on the filesystem, verified by automated filesystem inspection in tests.
- **SC-003**: Restarting the wrapper against a pre-existing data directory preserves 100% of previously written rows across at least 3 representative schemas, verified by round-trip tests.
- **SC-004**: Operators can successfully back up any single schema, any explicit list of schemas, or all schemas of an instance, and can later restore each of those selections into an empty target instance such that the restored content is byte-/row-equivalent to the source at backup time in at least 95% of representative test fixtures; the remaining cases (if any) fail cleanly with actionable errors rather than silent data loss.
- **SC-005**: A backup artifact produced on Linux restores successfully on Windows and vice versa in automated cross-platform tests.
- **SC-006**: Every failure mode enumerated in the Functional Requirements surfaces an error message that names the offending argument/path/schema/artifact, measured by a test suite that asserts on error content (target: 100% of enumerated failure modes covered).
- **SC-007**: An operator unfamiliar with the codebase can perform a full backup-then-restore cycle using only the command's built-in help text in under 10 minutes.
- **SC-008**: Running `backup` 10 times in quick succession against an instance produces 10 distinct timestamp-named containers in the configured backup location, with 0 overwritten or lost, verified by automated tests.
- **SC-009**: `restore --latest` always selects the chronologically most recent container according to the documented rule in 100% of test scenarios, and never proceeds without either an interactive confirmation or the explicit non-interactive "assume-yes" opt-in.

## Assumptions

- The existing `py_pglite → pglite-pydb` port (feature 001) already establishes the wrapper, packaging, and cross-platform (Linux + Windows/PowerShell) conventions this feature builds on.
- "Database" and "schema" are used as the user expressed them; in PostgreSQL/PGlite terms this feature treats a named PostgreSQL schema as the unit of selection for backup/restore. A single PGlite instance is assumed to hold one PostgreSQL database with multiple schemas; if a future need arises to back up multiple logical databases per instance, the selection model in FR-009/FR-016 extends naturally by treating database+schema as the selection key.
- Backup artifacts are intended for use with compatible `pglite-pydb` versions; cross-version restore compatibility is out of scope for this feature and will be addressed separately if needed.
- Network-mounted, read-only, or exotic filesystems (e.g., FUSE overlays) are out of scope; data directories are assumed to live on a local, writable filesystem.
- Encryption-at-rest and access control for backup artifacts are the operator's responsibility (standard filesystem permissions) and are out of scope for this feature.
- Hot-backup of a concurrently-writing instance is best-effort: the feature guarantees either an internally-consistent artifact or a clear failure, not zero-downtime backup semantics.
- Each PGlite instance has exactly one configured backup location at a time; multi-destination / mirrored backup configurations are out of scope for this feature.
- "Latest" is defined by the container's name-encoded timestamp under a single configured backup location. If the operator changes the backup location, older backups at the previous location are not considered by `--latest` until moved; this keeps the selection rule simple and auditable.
- Timestamp-name format will be chosen to sort lexicographically (e.g., `YYYYMMDD-HHMMSS[.fff]`) so that "latest" can be determined without parsing metadata, and so that operators can eyeball backup ordering in a file listing.
