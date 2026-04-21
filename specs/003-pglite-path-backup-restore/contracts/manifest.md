# Manifest Contract: `manifest.json` inside backup containers

**Feature**: 003-pglite-path-backup-restore | **Date**: 2026-04-21

Every backup container embeds exactly one `manifest.json` file at the top of its internal layout (see `data-model.md` §6). This document is the stable contract for that manifest's shape. `schema_version` is the hard compatibility gate: restore rejects unknown values with `CorruptContainerError` (FR-026) and an "upgrade `pglite-pydb`" message.

---

## Common fields (present in both kinds)

| Field | Type | Invariant |
|-------|------|-----------|
| `schema_version` | integer | `== 1` for this feature. |
| `kind` | string | `"logical"` or `"full-snapshot"`. Must match the container's filename-derived kind (FR-034). |
| `created_at` | string | ISO-8601 UTC, millisecond precision, trailing `Z`. Example: `"2026-04-21T14:30:02.517Z"`. Derived from the same timestamp used in the filename so the two cannot disagree. |
| `source_data_dir` | string | Resolved absolute real path of the instance that produced the container (FR-016). Informational; restore does not infer the target from it. |
| `pglite_pydb_version` | string | The CALVER version of `pglite-pydb` that produced the container (e.g. `"2026.4.21.2"`). Useful for debugging cross-version issues. |
| `postgres_server_version` | string | Exact text from `SELECT version();` executed against the PGlite instance at the moment of dump or snapshot. Useful for debugging future compatibility concerns. |
| `container_filename` | string | The final filename of the container (without directory). Cross-check against the actual filename on read; mismatch → `CorruptContainerError` (container was renamed by hand, manifest no longer matches). |

---

## Logical-only additional field

| Field | Type | Invariant |
|-------|------|-----------|
| `included_schemas` | array of strings | One entry per included schema in deterministic order. The sentinel `["*"]` (a single-element array whose only element is the literal string `"*"`) encodes the `--all` selection mode. An empty array is invalid. |

The `.sql` entries inside the container MUST be exactly `{schema}.sql` for each `schema` in `included_schemas` (modulo the `*` sentinel, which corresponds to one `.sql` per real user schema present at dump time).

---

## Full-snapshot-only expectations

No additional fields beyond the common set. `included_schemas` MUST be absent (not `null`, not `[]` — key not present) to make it trivially obvious that schema selection does not apply.

The archive's `data/` subtree MUST NOT contain a top-level `.pglite-pydb/` directory (FR-032, clarified 2026-04-21). Presence of that directory inside the archive → `CorruptContainerError` on read (it indicates the archive was produced by code that did not honour the clarification, or was tampered with).

---

## Example — logical

```json
{
  "schema_version": 1,
  "kind": "logical",
  "created_at": "2026-04-21T14:30:02.517Z",
  "source_data_dir": "/home/alice/project/pgdata",
  "pglite_pydb_version": "2026.4.21.2",
  "postgres_server_version": "PostgreSQL 16.4 on x86_64-pc-linux-gnu, compiled by gcc ...",
  "container_filename": "20260421-143002.517.tar.gz",
  "included_schemas": ["app", "analytics"]
}
```

## Example — full snapshot

```json
{
  "schema_version": 1,
  "kind": "full-snapshot",
  "created_at": "2026-04-21T14:31:15.004Z",
  "source_data_dir": "/home/alice/project/pgdata",
  "pglite_pydb_version": "2026.4.21.2",
  "postgres_server_version": "PostgreSQL 16.4 on x86_64-pc-linux-gnu, compiled by gcc ...",
  "container_filename": "FULL_SNAPSHOT_20260421-143115.004.tar.gz"
}
```

---

## Forward-compatibility

- New **optional** fields MAY be added in future features without bumping `schema_version`, provided readers that do not know about them still restore correctly.
- New **required** semantics MUST bump `schema_version` so old readers fail fast instead of producing wrong results.
- The file MUST remain valid JSON encoded as UTF-8 with no BOM, sorted keys, 2-space indentation (for human inspection via `tar -xOzf container.tar.gz <ts>/manifest.json | jq .`).
