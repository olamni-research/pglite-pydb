# Phase 0 Research: Mandatory Data Path + Backup/Restore

**Feature**: 003-pglite-path-backup-restore | **Date**: 2026-04-21

All `NEEDS CLARIFICATION` markers from the plan's Technical Context are resolved below. Seven research tracks (R1–R7) cover the non-obvious choices; everything else follows directly from the spec's 2026-04-21 clarification session.

---

## R1 — How logical dumps are produced (and restored)

**Decision**: Shell out to the stock PostgreSQL client binaries **`pg_dump`** (backup) and **`psql`** (restore), invoked against a PGlite server that `pglite-pydb` itself has started and bound to TCP on `127.0.0.1`. Both binaries must be present on the operator's `PATH`; their absence is a fail-fast error with an actionable message ("install PostgreSQL 15+ client tools, or set `PGLITE_PYDB_PG_DUMP` / `PGLITE_PYDB_PSQL` to an explicit path"). One schema per dump file (`<schema>.sql`) using `pg_dump --schema=<name> --format=plain --no-owner --no-privileges`; `--format=plain` keeps the artifact a text SQL script, portable and diff-able.

**Rationale**:

- PGlite speaks the Postgres wire protocol over its Node TCP server (already used by feature 001's Windows path), so any Postgres client works out of the box.
- `pg_dump` takes a **transactional snapshot** via `REPEATABLE READ` before emitting, which delivers FR-017's "internally consistent artifact" guarantee for free — whether we hold the FR-006 exclusive lock (default) or attach to a foreign running server (`--force-hot`). No torn writes are possible.
- `pg_dump` already handles every Postgres type, extension object, default, sequence, trigger, and permission correctly. Re-implementing that in Python is a multi-year maintenance tax for zero functional upside.
- The `--no-owner --no-privileges` flags strip host-local identities so the artifact restores cleanly onto a differently-owned target (SC-005 cross-platform portability).

**Alternatives considered**:

- **Python-side introspection with `psycopg` + `COPY TO`**: rejected — would re-invent `pg_dump` badly, miss edge cases, and still need a snapshot mechanism we'd have to build by hand.
- **PGlite's own `.dumpDataDir()` JS helper**: it exists but produces a *physical* snapshot of the WASM data directory, not a logical SQL dump; we reserve it for future optimisation of `--full-snapshot` only and even there prefer stdlib `tarfile` for simpler Python-side control.
- **Bundle `pg_dump` inside the Python wheel**: rejected — platform-specific binary, licence surface, maintenance burden; a documented PATH prereq is the standard PostgreSQL-ecosystem answer.

---

## R2 — Cross-platform advisory file lock (FR-006)

**Decision**: A new `pglite_pydb._lock.InstanceLock` context manager opens (or creates) `<data-dir>/.pglite-pydb/instance.lock` and acquires an exclusive, non-blocking advisory lock. On POSIX it uses `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`; on Windows it uses `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` on byte 0 of the lock file. Failure to acquire raises `InstanceInUseError` with the resolved path in the message. The lock file is intentionally left on disk between runs (it's a zero-byte marker) — its presence is not a lock, only its held file handle is.

**Rationale**:

- Both mechanisms are in the stdlib (no new Python deps).
- Advisory locks are automatically released on process death (kernel-level, not user-space), which survives `SIGKILL` / Task Manager termination and eliminates the "stale lock" class of bug a PID-file would introduce.
- `fcntl.flock` and `msvcrt.locking` are both process-scoped, which matches our requirement ("no two wrappers on the same host against the same resolved path"). Neither provides cross-host guarantees — correct, since the spec scopes FR-006 to "the same host".
- Symlinks are resolved to the canonical real path (FR-003) *before* deriving the lock file location, so two distinct symlinks pointing at the same real directory contend for the same lock as required by the edge-cases list.

**Alternatives considered**:

- **PID-file written atomically**: loses on abrupt termination — a crashed wrapper leaves a PID file that must be manually cleaned; conflicts with FR-006's "fail-fast" vs "prompt user to clean" contract.
- **`portalocker` third-party package**: nice API but a new runtime dep for ~30 lines of stdlib; rejected.
- **Advisory lock on the data directory itself** (`fcntl.flock` on the dir fd): unsupported on Windows via `msvcrt.locking`; asymmetric. The dedicated lock file under `.pglite-pydb/` keeps the two platforms symmetric.

---

## R3 — Timestamp format and sub-second uniqueness

**Decision**: Container filenames embed a UTC timestamp in the format **`YYYYMMDD-HHMMSS.fff`** — e.g. `20260421-143002.517`. Logical containers are named `<ts>.tar.gz`; full-snapshot containers are named `FULL_SNAPSHOT_<ts>.tar.gz`. Lexical sort == chronological sort; the millisecond suffix makes the probability of collision within one process negligible. On the rare event of an actual collision (sub-millisecond double invocation, or clock jitter), the backup engine appends `_<n>` with the smallest positive `n` that disambiguates (e.g. `20260421-143002.517_2.tar.gz`).

**Rationale**:

- Pure lexicographic "latest" = pure filesystem `sorted(...)[-1]` — no metadata parsing needed (matches the spec's Assumption on timestamp sort).
- UTC eliminates DST-induced non-monotonicity and cross-host ambiguity (SC-005).
- Milliseconds deliver SC-008's "10 rapid-fire backups produce 10 distinct names" without operator intervention; the `_<n>` fallback is belt-and-braces for wall-clock skew (spec edge case: "system clock moved backwards") and keeps the deterministic rule simple — `--latest` always picks the lexically highest name in scope, full stop.

**Alternatives considered**:

- **ISO-8601 with colons** (`2026-04-21T14:30:02.517Z`): colons are illegal in Windows filenames; rejected for portability.
- **Unix-epoch seconds**: loses human readability (SC-007's 10-minute-unfamiliar-operator criterion).
- **Nanosecond precision**: overkill, and not portably available on all filesystems' mtime.

---

## R4 — Tar layout for logical vs full-snapshot containers

**Decision**:

```text
# Logical container: <ts>.tar.gz
<ts>/                            # single top-level directory named the same as the ts
├── manifest.json                # kind: "logical", timestamp, source_data_dir, included_schemas, pglite_pydb_version, postgres_server_version
├── public.sql                   # one file per included schema, exact pg_dump --schema output
├── app.sql
└── analytics.sql

# Full-snapshot container: FULL_SNAPSHOT_<ts>.tar.gz
FULL_SNAPSHOT_<ts>/
├── manifest.json                # kind: "full-snapshot", timestamp, source_data_dir, pglite_pydb_version
└── data/                        # byte-exact copy of the data directory's file tree,
    ├── base/                    #   excluding the wrapper's sidecar `.pglite-pydb/` subtree
    ├── global/
    └── ...
```

Both archives are gzip-compressed tarballs. Python's stdlib `tarfile` module produces them; the `tarfile.PAX_FORMAT` format is selected so long paths and non-ASCII filenames round-trip identically between Linux and Windows (SC-005).

**Rationale**:

- The single top-level directory keeps `tar -xzf` safe (no top-level extraction pollution).
- Logical layout is human-inspectable with `tar -tzf` — an operator can see which schemas are inside without any tooling, matching SC-007.
- Full-snapshot omission of `.pglite-pydb/` directly encodes the 2026-04-21 clarification: the wrapper's instance-local pointer (backup-location config) must never leak into a restore target.
- PAX format is the only tar variant in the stdlib that preserves full Unicode filenames and paths longer than 100 bytes, which matters for Windows user profiles like `C:\Users\<name>\AppData\Local\...`.

**Alternatives considered**:

- **Zip**: no POSIX mode bits; rejected for eventual extensions that ship with executable helpers.
- **Uncompressed tar**: larger; no upside on local disk.
- **Per-table files** inside logical containers: PGlite is a single Postgres database; per-schema is the right selection granularity per spec.

---

## R5 — Why the sidecar lives inside the data directory (recording the "why")

**Decision**: Honoured as per the 2026-04-21 clarification: `<data-dir>/.pglite-pydb/config.json` is the only persistence point; no central registry.

**Rationale recorded** (so future maintainers understand the trade-off):

- **Portability**: moving an instance's data directory to another machine, user, or host also moves its backup-location config — the instance remains self-describing.
- **No privilege requirement**: writing into the user's own data directory never needs elevation; a central registry under `%PROGRAMDATA%` or `/etc` would.
- **Matches FR-003's canonical-identity rule**: the resolved real path is the instance ID, so anchoring per-instance state to that path is consistent.

**Trade-off accepted**: the sidecar is invisible to anyone listing instances without scanning the filesystem. That's acceptable because the spec explicitly rejects a registry, and listing instances is not a FR of this feature. If a future feature adds `pglite-pydb list-instances`, it can walk a user-provided search root.

---

## R6 — Interactive confirmation UX (shared FR-021, FR-022, FR-025, FR-035)

**Decision**: A single `_confirm(prompt: str, *, assume_yes: bool) -> bool` helper drives every prompt. It uses `sys.stdin.isatty()` for TTY detection. Behaviour:

| Context | `--assume-yes` absent | `--assume-yes` present |
|---------|-----------------------|------------------------|
| TTY | Interactive `[y/N]` prompt | Print "(auto-confirmed via --assume-yes)" and proceed |
| Non-TTY | Fail fast: "confirmation required but no TTY; pass --assume-yes" | Print "(auto-confirmed)" and proceed |

For FR-035's **second** confirmation (non-empty full-snapshot target), a **separate** flag `--assume-yes-destroy` is required; `--assume-yes` alone is insufficient. This encodes the spec's requirement that each confirmation needs its own explicit non-interactive opt-in and prevents an operator from auto-confirming destruction by accident.

**Rationale**:

- One helper = one place to test; the TTY/non-TTY matrix is tested directly with `pty` (POSIX) and `ConPTY`-free stubbing (Windows).
- The split `--assume-yes` vs `--assume-yes-destroy` means a conservative CI pipeline can always pass `--assume-yes` to unblock `--latest` without ever accidentally destroying a non-empty target.

**Alternatives considered**:

- **Single `--assume-yes` covering every prompt**: rejected — the spec explicitly calls out "separate from the first" for FR-035's second confirmation.
- **`yes` piped into stdin**: a common CI trick but masks the distinction between "confirmed" and "stdin was merely EOF-friendly"; rejected in favour of an explicit flag.

---

## R7 — "Completely empty" evaluation for FR-035

**Decision**: The helper `_is_target_completely_empty(path: Path) -> bool` returns `True` iff:

1. `path` does not exist, OR
2. `path` is a directory whose contents, after ignoring the allow-list below, are empty.

The allow-list (ignored when evaluating emptiness) is exactly what the 2026-04-21 clarification specifies: the wrapper's own `<path>/.pglite-pydb/` subtree, and the OS metadata files `.DS_Store`, `Thumbs.db`, `desktop.ini`. Any other file or subdirectory — including any file that looks like a PGlite data file, but also any unrelated content — triggers the second confirmation. The check is iterative (not recursive past the top level): any non-empty non-allow-listed entry at the top level is enough to flip emptiness to `False`.

**Rationale**:

- The top-level-only scan keeps the predicate fast and predictable; operators never need to reason about deep-tree content to anticipate whether the second prompt will fire.
- Symmetric allow-list on Linux and Windows (`Thumbs.db`/`desktop.ini` are inert on Linux but harmless; `.DS_Store` is inert on Windows but harmless) — no platform branching.

**Alternatives considered**:

- **Recursive emptiness**: a data directory with deeply nested empty dirs would incorrectly be treated as "non-empty"; rejected.
- **PGlite-specific signature sniffing** (e.g. "only trigger second prompt if a `PG_VERSION` file is present"): rejected because the clarification's rule is stricter — *any* unrecognised content triggers the prompt, which is the safer default.

---

## Cross-cutting: version bump

Feature 001's CALVER scheme (`YYYY.M.D.build`, with `D` always a real day) is the house rule. When this feature ships, the version bump is `2026.4.21.2` (or the next real-day build index on ship day). The plan does not bump the version at planning time.
