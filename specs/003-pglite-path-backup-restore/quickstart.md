# Quickstart: `pglite-pydb` Path + Backup/Restore

**Feature**: 003-pglite-path-backup-restore | **Date**: 2026-04-21 | **Audience**: operator unfamiliar with internals (SC-007)

This runbook walks an operator through the full lifecycle: install prerequisites, bootstrap a data directory, configure a backup location, run each of the three logical-backup selection modes, take a full snapshot, and restore in every supported mode including the two-stage confirmation for destructive full-snapshot restore. Works identically in `bash` (Linux/macOS) and `pwsh` (Windows) — only the path separators differ.

---

## 0. Prerequisites

On any supported OS:

```bash
# Python 3.10+, already present for pglite-pydb itself
python --version

# Node.js 20 LTS or 22 LTS (required by the PGlite WASM process)
node --version

# PostgreSQL 15+ client tools — NEW prerequisite for this feature
pg_dump --version
psql --version

# pglite-pydb itself
pip install "pglite-pydb>=2026.4.21.2"
pglite-pydb --version
```

If `pg_dump` or `psql` are installed but not on `PATH`, override them with environment variables:

```bash
export PGLITE_PYDB_PG_DUMP=/opt/pgsql-16/bin/pg_dump
export PGLITE_PYDB_PSQL=/opt/pgsql-16/bin/psql
```

On PowerShell:

```powershell
$env:PGLITE_PYDB_PG_DUMP = "C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"
$env:PGLITE_PYDB_PSQL     = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
```

---

## 1. Start an instance at an explicit path (US1)

```bash
# Pick any path — it will be created if missing.
export PGDATA=/tmp/pglite-demo            # bash
# $env:PGDATA = "D:\pglite-demo"          # pwsh

# From Python, the mandatory path is passed to PGliteConfig:
python - <<'PY'
from pglite_pydb import PGliteConfig, PGliteManager
cfg = PGliteConfig(data_dir="/tmp/pglite-demo")   # MANDATORY; omitting raises ValueError
with PGliteManager(cfg) as m:
    with m.connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS app;")
        cur.execute("CREATE TABLE IF NOT EXISTS app.widget (id int PRIMARY KEY, name text);")
        cur.execute("INSERT INTO app.widget VALUES (1,'alpha') ON CONFLICT DO NOTHING;")
        conn.commit()
PY

# Verify data landed ONLY inside the path (SC-002):
ls /tmp/pglite-demo                       # bash
# should list PG_VERSION, base/, global/, .pglite-pydb/, ...

# Stop the instance (context manager exit did this) and verify restart preserves data (SC-003):
python - <<'PY'
from pglite_pydb import PGliteConfig, PGliteManager
cfg = PGliteConfig(data_dir="/tmp/pglite-demo")
with PGliteManager(cfg) as m:
    with m.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM app.widget WHERE id = 1;")
        print(cur.fetchone())             # ('alpha',)
PY
```

**Acceptance mapping**: US1 Scenarios 1–5 (missing path fails; fresh path initialises; existing path opens without overwrite; concurrent paths isolated; invalid/occupied path rejected).

---

## 2. Configure a backup location (FR-008..FR-011)

```bash
mkdir -p /tmp/pglite-demo-backups
pglite-pydb config --data-dir /tmp/pglite-demo set-backup-location /tmp/pglite-demo-backups
pglite-pydb config --data-dir /tmp/pglite-demo get-backup-location
# → /tmp/pglite-demo-backups

pglite-pydb config --data-dir /tmp/pglite-demo show
# → pretty-printed sidecar JSON
```

The sidecar now lives at `/tmp/pglite-demo/.pglite-pydb/config.json`. Never hand-edit it; always mutate via `pglite-pydb config set-backup-location`.

---

## 3. Logical backup — three selection modes (US2)

```bash
# (a) single schema
pglite-pydb backup --data-dir /tmp/pglite-demo --schema app
# → /tmp/pglite-demo-backups/20260421-143002.517.tar.gz

# (b) explicit list
pglite-pydb backup --data-dir /tmp/pglite-demo --schema app --schema analytics

# (c) all user schemas
pglite-pydb backup --data-dir /tmp/pglite-demo --all
```

Each invocation produces a new timestamp-named container. Inspect one:

```bash
tar -tzf /tmp/pglite-demo-backups/20260421-143002.517.tar.gz
# 20260421-143002.517/manifest.json
# 20260421-143002.517/app.sql

tar -xOzf /tmp/pglite-demo-backups/20260421-143002.517.tar.gz 20260421-143002.517/manifest.json | jq .
# { "schema_version": 1, "kind": "logical", "created_at": "...Z", "included_schemas": ["app"], ... }
```

**Rapid-fire uniqueness (SC-008)**:

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do
  pglite-pydb backup --data-dir /tmp/pglite-demo --all &
done; wait
ls /tmp/pglite-demo-backups | wc -l
# → 10 (no overwrites; sub-second disambiguation kicks in if needed)
```

**Hot backup** (instance actively serving connections):

```bash
pglite-pydb backup --data-dir /tmp/pglite-demo --all --force-hot
```

Without `--force-hot`, `backup` acquires the FR-006 exclusive lock and fails fast if another process is connected.

---

## 4. Full snapshot (FR-031..FR-033)

```bash
pglite-pydb backup --data-dir /tmp/pglite-demo --full-snapshot
# → /tmp/pglite-demo-backups/FULL_SNAPSHOT_20260421-143115.004.tar.gz

tar -tzf .../FULL_SNAPSHOT_*.tar.gz | head
# FULL_SNAPSHOT_20260421-143115.004/manifest.json
# FULL_SNAPSHOT_20260421-143115.004/data/PG_VERSION
# FULL_SNAPSHOT_20260421-143115.004/data/base/...
# (NO .pglite-pydb/ inside — excluded by FR-032)
```

No `--force-hot` option exists for full snapshots: they always require the exclusive lock.

---

## 5. Restore — logical, by name (US3 Scenario 1)

```bash
# Bootstrap an empty target at a fresh path:
mkdir -p /tmp/pglite-restored

# Restore the 'app' schema from a specific container:
pglite-pydb restore \
    --data-dir /tmp/pglite-restored \
    /tmp/pglite-demo-backups/20260421-143002.517.tar.gz

python - <<'PY'
from pglite_pydb import PGliteConfig, PGliteManager
with PGliteManager(PGliteConfig(data_dir="/tmp/pglite-restored")) as m:
    with m.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM app.widget WHERE id=1;")
        print(cur.fetchone())             # ('alpha',)
PY
```

---

## 6. Restore — `--latest` with confirmation (US3 Scenarios 3–4)

```bash
# Point at the configured backup location via --data-dir's sidecar:
pglite-pydb config --data-dir /tmp/pglite-restored set-backup-location /tmp/pglite-demo-backups
pglite-pydb restore --data-dir /tmp/pglite-restored --latest
# pglite-pydb: selected container: 20260421-143059.812.tar.gz
# pglite-pydb:   created_at:       2026-04-21T14:30:59.812Z
# pglite-pydb:   included_schemas: ["app","analytics"]
# Proceed with restore? [y/N] y
```

Decline the prompt (`n` or EOF) → exit 15 (`ConfirmationDeclinedError`), no changes.

Non-TTY (e.g. CI pipeline):

```bash
pglite-pydb restore --data-dir /tmp/pglite-restored --latest --assume-yes
```

Omitting `--assume-yes` in a non-TTY context → exit 14.

---

## 7. Restore — `--overwrite` with conflict listing (US3 Scenarios 7–8)

```bash
# Target already contains schema 'app' with different data.
pglite-pydb restore \
    --data-dir /tmp/pglite-restored \
    /tmp/pglite-demo-backups/20260421-143002.517.tar.gz
# exit 13: RestoreConflictError: schemas in target would be replaced: [app]
# Pass --overwrite to proceed.

pglite-pydb restore \
    --data-dir /tmp/pglite-restored \
    /tmp/pglite-demo-backups/20260421-143002.517.tar.gz \
    --overwrite
# pglite-pydb: the following schemas will be REPLACED: [app]
# Proceed? [y/N] y
```

Same TTY / `--assume-yes` rules as `--latest`.

---

## 8. Full-snapshot restore — two-stage confirmation (FR-035)

```bash
# Target is empty:
rm -rf /tmp/pglite-fresh && mkdir /tmp/pglite-fresh
pglite-pydb config --data-dir /tmp/pglite-fresh set-backup-location /tmp/pglite-demo-backups
pglite-pydb restore --data-dir /tmp/pglite-fresh --full-snapshot --latest
# → single confirmation, proceeds.

# Target NOT empty (has PG data from earlier restore):
pglite-pydb restore --data-dir /tmp/pglite-restored --full-snapshot --latest
# pglite-pydb: selected full-snapshot container: FULL_SNAPSHOT_20260421-143115.004.tar.gz
# ...
# Proceed with full-snapshot restore into /tmp/pglite-restored? [y/N] y
# pglite-pydb: target data directory is NOT empty. All non-.pglite-pydb content will be destroyed.
# Type the word DESTROY to confirm (or anything else to abort): DESTROY
```

Non-TTY destructive restore requires **both** flags:

```bash
pglite-pydb restore --data-dir /tmp/pglite-restored --full-snapshot --latest \
    --assume-yes --assume-yes-destroy
```

Omitting `--assume-yes-destroy` over a non-empty target in non-TTY context → exit 14, even if `--assume-yes` is present.

---

## 9. Sidecar preservation across full-snapshot restore

After a full-snapshot restore, inspect the sidecar:

```bash
cat /tmp/pglite-restored/.pglite-pydb/config.json
# Shows the TARGET's own backup_location (preserved), not the source's.
# If the target had no sidecar, none is created: reconfigure before next backup:
pglite-pydb config --data-dir /tmp/pglite-fresh set-backup-location /tmp/pglite-demo-backups
```

---

## 10. Cross-platform portability check (SC-005)

Produce on Linux, restore on Windows (or vice versa):

```bash
# Linux:
pglite-pydb backup --data-dir /tmp/pglite-demo --all
scp /tmp/pglite-demo-backups/20260421-143002.517.tar.gz windows-host:D:/pglite-backups/
```

```powershell
# Windows:
pglite-pydb restore --data-dir D:\pglite-target D:\pglite-backups\20260421-143002.517.tar.gz
```

Both the SQL content (plain-format, no-owner, no-privileges) and the archive layout (PAX UTF-8) make this round-trip reliable.

---

## Minimal CI smoke test

```bash
set -e
D=$(mktemp -d)
B=$(mktemp -d)
python -c "from pglite_pydb import PGliteConfig, PGliteManager; \
  cfg=PGliteConfig(data_dir='$D'); \
  m=PGliteManager(cfg); m.start(); \
  c=m.connect(); cur=c.cursor(); \
  cur.execute('CREATE SCHEMA app'); \
  cur.execute('CREATE TABLE app.t(x int)'); \
  cur.execute('INSERT INTO app.t VALUES (1)'); \
  c.commit(); m.stop()"
pglite-pydb config --data-dir "$D" set-backup-location "$B"
pglite-pydb backup --data-dir "$D" --all
D2=$(mktemp -d)
pglite-pydb restore --data-dir "$D2" "$B"/*.tar.gz
python -c "from pglite_pydb import PGliteConfig, PGliteManager; \
  m=PGliteManager(PGliteConfig(data_dir='$D2')); m.start(); \
  c=m.connect(); cur=c.cursor(); \
  cur.execute('SELECT x FROM app.t'); \
  assert cur.fetchone() == (1,); m.stop()"
echo OK
```

End-to-end in under 30 s on any of the three supported platforms.
