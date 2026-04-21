# Phase 1 Data Model: Mandatory Data Path + Backup/Restore

**Feature**: 003-pglite-path-backup-restore | **Date**: 2026-04-21

This document catalogues the entities this feature introduces or mutates, their fields and invariants, and the state diagrams for `backup` and `restore`. It does not prescribe class shapes — the Python implementation may collapse some of these into plain dataclasses, dicts, or simple tuples. What matters is that every invariant is enforced somewhere.

---

## 1. `PGliteInstance`

A single managed PGlite database process (or a stopped configuration record of one) on a host.

| Field | Type | Source / invariant |
|-------|------|--------------------|
| `data_dir` | `Path` (absolute, resolved real path) | **Primary key** on a host. Mandatory. Computed via `Path(raw).resolve(strict=False)` so symlinks are followed (FR-003). |
| `sidecar_dir` | `Path` | Always `data_dir / ".pglite-pydb"`. Created lazily on first wrapper start or first config write. |
| `lock_file` | `Path` | Always `sidecar_dir / "instance.lock"`. Zero-byte marker; not a lock in itself — the held fd is. |
| `config` | `SidecarConfig` | Persisted state (see §3); loaded lazily. |

**Invariants**:

- `data_dir` is always absolute and symlink-resolved before use (FR-003).
- Two `PGliteInstance`s on the same host with the same `data_dir` are the *same* instance — they must contend for the same `InstanceLock` (FR-006).
- `data_dir` either does not exist, or is a directory. A path that exists as a regular file is an error (FR-005).

---

## 2. `DataDirectory`

The filesystem state at `PGliteInstance.data_dir`. Not a class of its own — a set of predicates and helpers operating on the path.

| Predicate | Meaning |
|-----------|---------|
| `is_fresh(dir)` | Directory does not exist, or exists and contains no entries at all. Triggers a fresh PGlite init (FR-004). |
| `is_existing_pglite_instance(dir)` | Directory contains a recognisable PGlite data layout (e.g. `PG_VERSION` or PGlite's own marker file). Triggers "open existing" (FR-004, no overwrite). |
| `is_rejectable(dir)` | Exists as a non-directory, OR is a non-empty directory without a PGlite layout. Triggers FR-005's rejection. |
| `is_completely_empty_for_full_snapshot_restore(dir)` | See research §R7. Directory missing OR contains only allow-listed entries (`.pglite-pydb/`, `.DS_Store`, `Thumbs.db`, `desktop.ini`). Governs FR-035's second confirmation. |

**Invariants**:

- Exactly one of `is_fresh`, `is_existing_pglite_instance`, `is_rejectable` holds. The allocator picks one and acts on it atomically under the `InstanceLock`.

---

## 3. `SidecarConfig`

Persisted per-instance state, serialised as JSON at `<data-dir>/.pglite-pydb/config.json`.

```jsonc
{
  "schema_version": 1,
  "backup_location": "/abs/resolved/path/to/backups"  // null if not yet configured
}
```

| Field | Type | Invariant |
|-------|------|-----------|
| `schema_version` | `int` | `== 1` for this feature; future features bump it. Unknown versions cause a fail-fast error telling the operator to upgrade `pglite-pydb`. |
| `backup_location` | `str` (absolute resolved path) \| `null` | When non-null, always an absolute path with symlinks resolved. `null` means "not yet configured" — FR-010 requires configuring one before `backup` or `restore --latest` can run. |

**Access rules**:

- Read/written only through `pglite_pydb.config.SidecarConfig.load(data_dir)` / `.save()`.
- Never hand-edited (documented in `cli.md`). The wrapper's `pglite-pydb config set-backup-location <path>` is the mutation surface (FR-009).
- Excluded from full-snapshot archives (FR-032). Preserved as-is on `restore --full-snapshot` (FR-036, clarified 2026-04-21).

---

## 4. `InstanceLock`

A cross-platform advisory file lock on `<data-dir>/.pglite-pydb/instance.lock`. See research §R2.

| Field | Type | Notes |
|-------|------|-------|
| `path` | `Path` | Fixed location. |
| `fd` | OS file descriptor | Held for the lock's lifetime. |
| `mechanism` | `"fcntl"` \| `"msvcrt"` | Chosen at import time from `_platform.IS_WINDOWS`. |

**State machine**:

```
    unacquired ──acquire()──► held ──release() / process exit──► unacquired
        │
        └──acquire() fails───► InstanceInUseError (FR-006)
```

Acquisition is **non-blocking**: if the lock is held by another process, `acquire()` fails immediately with `InstanceInUseError(resolved_data_dir)`. Release is guaranteed by the OS on process death.

---

## 5. `BackupLocation`

A resolved absolute directory on the local filesystem where containers accumulate. Derived from `SidecarConfig.backup_location`.

| Predicate | Meaning |
|-----------|---------|
| `exists_and_writable(path)` | Directory exists and the current process can create files in it. Failure → FR-006-style clear error naming the path (the spec's FR-006 language for backup-location failures). |
| `list_logical_containers(path)` | Returns filenames matching `^\d{8}-\d{6}\.\d{3}(_\d+)?\.tar\.gz$`, sorted lexically ascending. |
| `list_full_snapshot_containers(path)` | Returns filenames matching `^FULL_SNAPSHOT_\d{8}-\d{6}\.\d{3}(_\d+)?\.tar\.gz$`, sorted lexically ascending. |
| `latest(path, kind)` | `list_..._containers(path, kind)[-1]` or raises if empty. Enforces FR-021 scoping: each `--latest` is scoped to its own container kind (never crosses). |

**Invariants**:

- "Latest" is purely lexicographic — no parsing of the manifest, no filesystem mtime. The timestamp format (research §R3) makes lexical == chronological.
- Empty location + `--latest` → distinct error from "no location configured" (FR-023).

---

## 6. `BackupContainer` — abstract, with two variants

A single `.tar.gz` file living in a `BackupLocation`. Two concrete variants:

### 6a. `LogicalContainer`

- **Filename grammar**: `^(?P<ts>\d{8}-\d{6}\.\d{3})(_\d+)?\.tar\.gz$`
- **Internal layout** (see research §R4):
  ```
  <ts>/manifest.json
  <ts>/<schema>.sql        # one per included schema
  ```
- **Manifest shape**: see §7, `kind == "logical"`.

### 6b. `FullSnapshotContainer`

- **Filename grammar**: `^FULL_SNAPSHOT_(?P<ts>\d{8}-\d{6}\.\d{3})(_\d+)?\.tar\.gz$`
- **Internal layout**:
  ```
  FULL_SNAPSHOT_<ts>/manifest.json
  FULL_SNAPSHOT_<ts>/data/...     # full data-dir tree EXCEPT .pglite-pydb/
  ```
- **Manifest shape**: see §7, `kind == "full-snapshot"`.

**Cross-variant invariants**:

- Filename prefix is authoritative for dispatch. `restore` (logical) must refuse a `FULL_SNAPSHOT_*` filename; `restore --full-snapshot` must refuse anything else (FR-034).
- Every container embeds its own `manifest.json`; `kind` field must match the filename-derived kind or the container is rejected as corrupt (FR-026).

---

## 7. `Manifest`

JSON blob inside every container. Two shapes:

### 7a. Logical manifest

```jsonc
{
  "schema_version": 1,
  "kind": "logical",
  "created_at": "2026-04-21T14:30:02.517Z",          // ISO-8601 UTC, millisecond precision
  "source_data_dir": "/abs/resolved/source",         // FR-016
  "included_schemas": ["app", "analytics"],          // or ["*"] sentinel for "all" mode
  "pglite_pydb_version": "2026.4.21.2",
  "postgres_server_version": "PostgreSQL 16.x ..."    // from SELECT version() at dump time
}
```

### 7b. Full-snapshot manifest

```jsonc
{
  "schema_version": 1,
  "kind": "full-snapshot",
  "created_at": "2026-04-21T14:30:02.517Z",
  "source_data_dir": "/abs/resolved/source",
  "pglite_pydb_version": "2026.4.21.2",
  "postgres_server_version": "PostgreSQL 16.x ..."
}
```

**Invariants**:

- `schema_version` is a hard gate: unknown values cause rejection with an "upgrade pglite-pydb" hint (FR-026).
- `source_data_dir` is informational only — restore targets are supplied by the operator, not inferred from the manifest.
- `included_schemas` is absent from full-snapshot manifests (no schema selection applies per FR-031).
- `included_schemas == ["*"]` encodes the "all" selection mode; this is distinguishable from the explicit list `["app", "analytics", ...]`.

---

## 8. `SchemaSelection`

A tagged value passed to `BackupEngine.create_logical`:

| Variant | CLI form | Meaning |
|---------|----------|---------|
| `Single("app")` | `backup --schema app` | One schema. |
| `Many(["app", "analytics"])` | `backup --schema app --schema analytics` (repeatable) | Explicit list, deduplicated, preserving first-seen order. |
| `All` | `backup --all` | Every user schema (pg_catalog / information_schema / pg_toast / pglite's internals excluded). |

**Invariants**:

- Exactly one variant per invocation; `--schema ... --all` is a usage error.
- Every named schema must exist at dump time or the entire invocation fails with no partial container (FR-015).
- `All` against a schemaless instance produces a valid empty-but-well-formed logical container whose `included_schemas == ["*"]` (edge case: zero user schemas).

---

## 9. `BackupEngine` — operation state diagrams

### 9a. `create_logical(instance, selection, *, force_hot=False)`

```
[start]
  │
  ├──► validate backup_location configured            (fail → FR-010 error)
  ├──► resolve schemas (if Single/Many)               (fail → FR-015 error)
  ├──► force_hot? ──yes──► (skip lock; attach to running server)
  │         │
  │         no
  │         │
  │         ▼
  │    acquire InstanceLock                           (fail → FR-017 "instance in use")
  │    start PGlite TCP server under lock
  │
  ├──► compute ts, pick filename (disambiguate if needed)
  ├──► open tempfile .tar.gz.partial in backup_location
  ├──► for each schema: pg_dump --schema --format=plain → write entry
  ├──► write manifest.json
  ├──► close tar; atomic rename .partial → final name
  ├──► stop PGlite server (if started by us); release lock
  │
  └──► [done — container path returned]

 on ANY error: delete .partial, release resources, re-raise with actionable message (FR-017 cleanup)
```

### 9b. `create_full_snapshot(instance)`

```
[start]
  │
  ├──► validate backup_location configured
  ├──► acquire InstanceLock (REQUIRED — no --force-hot, per FR-033)
  ├──► compute ts, pick filename (FULL_SNAPSHOT_<ts>.tar.gz, disambiguate if needed)
  ├──► open tempfile .tar.gz.partial
  ├──► walk data_dir, excluding <data-dir>/.pglite-pydb/, add entries to tar
  ├──► write manifest.json with kind="full-snapshot"
  ├──► atomic rename .partial → final name
  ├──► release lock
  │
  └──► [done]
```

### 9c. `restore_logical(instance, containers, *, overwrite=False, assume_yes=False)`

```
[start]
  │
  ├──► if containers is "--latest": pick BackupLocation.latest(kind=logical)
  │         (fail → FR-023 error if none; FR-020 if no selector provided at all)
  ├──► confirm selection if --latest (FR-021 / FR-022)
  │
  ├──► for each container: validate filename prefix, open tar, read manifest
  │         reject if corrupt / wrong kind / unknown schema_version (FR-026)
  ├──► collect union of schemas to be restored
  ├──► open target instance (acquire lock, start TCP server)
  ├──► detect existing schemas that would conflict
  │         if conflicts && !overwrite: fail FR-025 with names
  │         if conflicts &&  overwrite: list them, confirm (FR-025 TTY/--assume-yes rules)
  ├──► psql < <schema>.sql for each schema, in a single transaction per container
  │         on failure: ROLLBACK, nothing visible to clients (FR-027)
  ├──► stop server, release lock
  │
  └──► [done]
```

### 9d. `restore_full_snapshot(instance, container, *, assume_yes=False, assume_yes_destroy=False)`

```
[start]
  │
  ├──► if container is "--latest": pick BackupLocation.latest(kind=full-snapshot)
  ├──► validate filename prefix is FULL_SNAPSHOT_; reject otherwise (FR-034)
  ├──► confirm #1: display manifest, require confirmation (FR-035)
  ├──► if target is NOT completely_empty (R7 helper):
  │         confirm #2: explicit destruction acknowledgment
  │         non-TTY requires --assume-yes-destroy (research §R6)
  ├──► acquire InstanceLock on target (FR-036)
  ├──► preserve target's existing .pglite-pydb/ (if any) — stash in-memory
  ├──► extract tar into target data_dir (replacing contents)
  ├──► restore preserved .pglite-pydb/ (if any) OR leave absent (FR-036 / clarification)
  ├──► release lock
  │
  └──► [done]

 on failure mid-extraction: mark target with a sentinel file `.pglite-pydb/FAILED_RESTORE`
   so future wrapper starts refuse to treat it as a valid instance until the operator clears it.
```

---

## 10. Error taxonomy (one class per FR failure mode)

Every distinct failure mode the spec enumerates has a named exception with an actionable message. The CLI maps each to a distinct exit code (see `contracts/cli.md`).

| Exception | Raised for | Spec ref |
|-----------|-----------|----------|
| `MissingDataDirError` | Data-dir argument absent | FR-001, FR-028 |
| `InvalidDataDirError` | Path is a file / unrelated non-empty dir | FR-005 |
| `InstanceInUseError` | Lock contention | FR-006, FR-017, FR-033 |
| `BackupLocationNotConfiguredError` | No sidecar config | FR-010, FR-023 |
| `BackupLocationUnavailableError` | Missing / unwritable / disconnected | FR-011, edge cases |
| `SchemaNotFoundError(name)` | Named schema absent at dump time | FR-015 |
| `NoBackupsFoundError` | `--latest` with empty scope | FR-023 |
| `BackupSelectorMissingError` | Neither name nor `--latest` passed | FR-020 |
| `ContainerKindMismatchError` | `FULL_SNAPSHOT_*` to `restore` or vice versa | FR-034 |
| `CorruptContainerError(name, reason)` | Tar unreadable / manifest wrong / schema_version unknown | FR-026 |
| `RestoreConflictError(schemas)` | Existing schema, no `--overwrite` | FR-025 |
| `ConfirmationRequiredError` | Non-TTY, no `--assume-yes[-destroy]` | FR-022, FR-035 |
| `ConfirmationDeclinedError` | Interactive user said no | FR-021 (abort path) |

All error messages include the offending argument / path / schema / container name (FR-030).
