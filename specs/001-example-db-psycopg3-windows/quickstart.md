# Quickstart: Windows Sample Database Example

**Feature**: `001-example-db-psycopg3-windows`
**Audience**: A developer on Windows 10/11 who just cloned the repo and wants to see the example work.
**Target**: clone-to-output in under 10 minutes (spec SC-001).

---

## Prerequisites (one-time)

1. **Windows 10 build 17763+ or Windows 11** (64-bit).
2. **Python** ≥ 3.10 on `PATH`.
3. **Node.js** ≥ 18 on `PATH` (needed by PGlite's bridge — same requirement as the host `py-pglite` package).
4. **`uv`** (recommended) or `pip` for installing Python dependencies.

> Non-Windows machines: the example and its tests will refuse to run (spec FR-011). Use a Windows host or a Windows CI runner.

## Step 1 — Install Python dependencies

From the repo root:

```powershell
uv sync --extra psycopg
uv pip install pywin32
```

This installs `py-pglite`, `psycopg[binary]`, `pytest`, and the Windows-specific `pywin32` needed by the named-pipe transport adapter.

## Step 2 — Verify Node-side PGlite is available

From the repo root:

```powershell
npm install   # idempotent; reuses node_modules already present in the repo
```

## Step 3 — Run the example over TCP (default)

From the repo root:

```powershell
python -m examples.windows_sample_db.run_example
```

Expected output (abridged):

```text
[example] transport=tcp host=127.0.0.1 port=54320 pipe=- data_dir=<...>\data\pgdata
[example] dump=ok sha256=... (<N> bytes)
[example] pgdata status=fresh
[example] procedures installed=10 of 10
[example] connected as role=example_user transport=tcp
[example] proc=get_customer_by_email rows=1 elapsed_ms=3.2
[example] proc=list_articles_for_product rows=10 elapsed_ms=5.1
[example] proc=count_articles_per_product rows=N elapsed_ms=...
[example] proc=top_products_by_revenue rows=5 elapsed_ms=...
[example] proc=list_orders_for_customer rows=N elapsed_ms=...
[example] proc=articles_in_category rows=N elapsed_ms=...
[example] proc=customer_order_report rows=1 elapsed_ms=...
[example] proc=bulk_log_articles_for_product rows=N elapsed_ms=...
[example] proc=rename_product_display_name rows=0 elapsed_ms=...
[example] proc=assert_product_exists rows=0 elapsed_ms=...
[example] done exit=0
```

First run includes the dump load and procedure install (≤ 60 s on a modern laptop). Every later run reuses the on-disk data and starts in < 10 s (SC-002).

## Step 4 — Run it again over the named-pipe transport

```powershell
python -m examples.windows_sample_db.run_example --transport pipe
```

You should see `transport=pipe` in the first log line and `pipe=\\.\pipe\pglite_example` in place of `port=...`. Procedure rows and elapsed times should match the TCP run within noise.

To prove no TCP listener was opened during this run, inspect with `Get-NetTCPConnection -LocalPort 54320` in another PowerShell — you should find nothing.

## Step 5 — Run the Windows-only test suite

From the repo root:

```powershell
pytest tests/windows_sample_db -v
```

Expected: every test passes. Each test that exercises a stored procedure runs **twice** (once per transport) because the transport fixture is parametrized. The suite also covers:
- Dump checksum verification (`test_loader.py`)
- Idempotent restart on warm data (`test_persistence.py`)
- Negative-path error handling for every procedure (`test_procedures_errors.py`)
- TCP port-in-use and pipe-collision failure modes (`test_transport_*.py`)
- Temp-directory cleanup (`test_cleanup.py`)

Running the same command on Linux/macOS exits with code 0 and reports zero tests run (SC-004).

## Step 6 — Reset and reload (optional)

```powershell
python -m examples.windows_sample_db.run_example --reset
```

Wipes the on-disk data directory and re-runs the first-time load. Use this if you pull an updated vendored dump.

## Step 7 — Connect with your own tool (optional)

The example exposes Postgres wire protocol on both transports, so standard tools work:

```powershell
# TCP — any libpq-based client
psql "host=127.0.0.1 port=54320 user=example_user dbname=postgres"
```

For the named-pipe transport, connect your own client directly at `\\.\pipe\pglite_example` — works with any tool that opens a Windows pipe and speaks Postgres wire protocol.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Exit code `2` | Running on non-Windows | Use a Windows host. |
| Exit code `3`, "dump file tampered" | `sample_db.sql` modified after commit | `git restore examples/windows_sample_db/data/sample_db.sql`. |
| Exit code `4`, "transport unavailable" | Pipe creation blocked (group policy, AV) | Rerun with `--transport tcp`, or loosen policy. |
| Exit code `5`, port `54320` in use | Prior run still holding the port | `Get-Process -Id (Get-NetTCPConnection -LocalPort 54320).OwningProcess \| Stop-Process`, or pass `--port 54321`. |
| Exit code `5`, pipe name in use | Prior crashed run left a stale pipe OR a second example running | Rerun with `--unique-pipe`. |
| Exit code `6`, "data directory inconsistent" | Earlier run was killed mid-load | Rerun with `--reset`. |
