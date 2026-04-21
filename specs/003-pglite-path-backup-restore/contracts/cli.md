# CLI Contract: `pglite-pydb`

**Feature**: 003-pglite-path-backup-restore | **Date**: 2026-04-21

The `pglite-pydb` console script is registered via `[project.scripts]` in `pyproject.toml`:

```toml
[project.scripts]
pglite-pydb = "pglite_pydb.cli.main:main"
```

The CLI is the **sole stable mutation surface** for the sidecar config and for backup/restore operations. It is implemented with stdlib `argparse` (no new dependency). This document is the stable contract for its argument grammar, exit codes, and stderr messages.

---

## Top-level invocation

```
pglite-pydb [--version] [--help] <command> [<args>...]
```

**Top-level exit codes** (shared by all commands):

| Code | Meaning |
|------|---------|
| `0` | Success. |
| `1` | Generic runtime failure (uncategorised). |
| `2` | Usage error — bad flag combination, unknown subcommand, missing required argument. Argparse-default. |
| `3` | `MissingDataDirError` — FR-001 / FR-028. |
| `4` | `InvalidDataDirError` — FR-005. |
| `5` | `InstanceInUseError` — FR-006 / FR-017 / FR-033. |
| `6` | `BackupLocationNotConfiguredError` — FR-010 / FR-023 (case: not configured). |
| `7` | `BackupLocationUnavailableError` — FR-011. |
| `8` | `SchemaNotFoundError` — FR-015. |
| `9` | `NoBackupsFoundError` — FR-023 (case: configured but empty). |
| `10` | `BackupSelectorMissingError` — FR-020. |
| `11` | `ContainerKindMismatchError` — FR-034. |
| `12` | `CorruptContainerError` — FR-026. |
| `13` | `RestoreConflictError` — FR-025 (conflict without `--overwrite`). |
| `14` | `ConfirmationRequiredError` — FR-022 / FR-035 (non-TTY, no assume-yes). |
| `15` | `ConfirmationDeclinedError` — FR-021 abort path. |

All errors write a single-line, actionable message to **stderr** beginning with `pglite-pydb: error: `. The message names the offending argument / path / schema / container (FR-030).

---

## `pglite-pydb backup`

```
pglite-pydb backup --data-dir <PATH>
                   ( --schema <NAME>... | --all | --full-snapshot )
                   [--force-hot]
```

**Arguments**:

| Flag | Required | Meaning |
|------|:--:|---------|
| `--data-dir <PATH>` | ✔ | Target instance data directory. Resolved via `Path.resolve()` (follows symlinks, FR-003). Absent → exit 3. |
| `--schema <NAME>` | ▲ one-of | Repeatable. At least one occurrence selects "single or list" mode. |
| `--all` | ▲ one-of | All user schemas. |
| `--full-snapshot` | ▲ one-of | Physical snapshot; schema selection not applicable. |
| `--force-hot` | | Logical mode only. Skip FR-006 lock; attach to a running server. Forbidden with `--full-snapshot` (exit 2). Forbidden without one of the logical-selection flags (exit 2). |

Exactly **one** of `--schema...` / `--all` / `--full-snapshot` must be supplied. Zero or more-than-one → exit 2.

**Stdout on success** (one line):

```
<absolute-path-to-new-container>
```

**Stderr on start** (informational, always):

```
pglite-pydb: instance data dir:    <resolved-data-dir>
pglite-pydb: backup location:      <resolved-backup-location>
```

**Failure catalogue**:

| Condition | Exit | Stderr |
|-----------|:--:|--------|
| Data-dir missing / not a directory / rejectable | 3 or 4 | names the path and what's wrong |
| Backup location not configured | 6 | names the instance + how to configure (`pglite-pydb config set-backup-location`) |
| Backup location missing / unwritable / disconnected | 7 | names the location |
| Requested schema absent at dump time | 8 | names the schema |
| Lock held by another process (default) | 5 | names the data dir + "already in use" |
| `pg_dump` missing on PATH | 1 | "pg_dump not found on PATH; install PostgreSQL 15+ client tools or set $PGLITE_PYDB_PG_DUMP" |
| Mid-archive error | 1 or specific | `.partial` removed; original containers intact (FR-014, FR-017) |

---

## `pglite-pydb restore`

```
pglite-pydb restore --data-dir <PATH>
                    ( <container>... | --latest | --full-snapshot ( <container> | --latest ) )
                    [--overwrite]
                    [--assume-yes]
                    [--assume-yes-destroy]      # only meaningful with --full-snapshot
```

**Arguments**:

| Flag | Required | Meaning |
|------|:--:|---------|
| `--data-dir <PATH>` | ✔ | Target instance. Absent → exit 3. |
| `<container>...` | ▲ one-of | One or more paths (or bare filenames relative to the configured backup location) of logical containers. |
| `--latest` | ▲ one-of | Logical mode: most recent logical container in the backup location. |
| `--full-snapshot` | | Switches to full-snapshot mode. Must be followed by either a single `<container>` or `--latest`. |
| `--overwrite` | | Allow replacing existing schemas (logical mode only). Triggers a pre-apply listing + confirmation (FR-025). |
| `--assume-yes` | | Auto-confirms the primary `--latest` and `--overwrite` prompts. Required in non-TTY contexts (FR-022, FR-025). |
| `--assume-yes-destroy` | | Required in non-TTY contexts for the FR-035 **second** confirmation when restoring a full-snapshot over a non-empty target. Never implied by `--assume-yes`. |

**Mode exclusivity**:

- Logical: at least one `<container>` OR `--latest`; never both. Neither → exit 10.
- Full-snapshot: exactly one `<container>` OR `--latest` after `--full-snapshot`; `--overwrite` is rejected (exit 2) because full-snapshot replacement is whole-instance and governed instead by the two-stage confirmation.

**Stderr flow (full-snapshot, non-empty target, TTY)**:

```
pglite-pydb: selected full-snapshot container: FULL_SNAPSHOT_<ts>.tar.gz
pglite-pydb:   created_at:         2026-04-21T14:30:02.517Z
pglite-pydb:   source_data_dir:    /abs/resolved/source
Proceed with full-snapshot restore into <target-data-dir>? [y/N] y
pglite-pydb: target data directory is NOT empty. All non-.pglite-pydb content will be destroyed.
Type the word DESTROY to confirm (or anything else to abort): DESTROY
```

Non-TTY variant requires **both** `--assume-yes` and `--assume-yes-destroy`.

**Failure catalogue**: every class from the error taxonomy in `data-model.md` §10 maps to its exit code in the top-level table.

---

## `pglite-pydb config`

```
pglite-pydb config --data-dir <PATH> set-backup-location <LOCATION>
pglite-pydb config --data-dir <PATH> get-backup-location
pglite-pydb config --data-dir <PATH> show
```

| Sub | Stdout | Purpose |
|-----|--------|---------|
| `set-backup-location <LOCATION>` | resolved absolute path written | Persists `backup_location` into `<data-dir>/.pglite-pydb/config.json`. Creates the sidecar dir if missing. `LOCATION` is resolved via `Path.resolve(strict=False)`; the directory itself is **not** required to exist at config time (it will be checked at `backup` time — FR-011). |
| `get-backup-location` | one line: the resolved path OR `(not configured)` | Inspectable per FR-009. |
| `show` | JSON body of the sidecar config (pretty-printed) | Full inspection for debugging. |

The `config` command never runs PGlite and never acquires the `InstanceLock` — it only touches the sidecar file.

---

## Exit-code stability

The exit-code table above is part of the stable contract. Future features MAY add new codes; they MUST NOT renumber existing ones. This allows CI pipelines to branch on specific failure classes (e.g. "skip restore test if backup location not configured" → `code == 6`).

---

## `--help` output

Every command supports `--help` (argparse default). `--help` output for the top-level script must list all subcommands with a one-line description, enabling SC-007 ("operator unfamiliar with the codebase can perform a full backup-then-restore cycle using only the command's built-in help text in under 10 minutes").
