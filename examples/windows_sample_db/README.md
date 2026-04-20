# Windows Sample Database Example

Windows-only runnable example that loads the JannikArndt
`PostgreSQLSampleDatabase` dump into a persisted PGlite data directory,
installs 10 named stored procedures, and lets the same `psycopg 3`
client reach the database over either **TCP** or a **Windows named
pipe** — selected at run time.

**Status**: scaffolding only (Slice A of the spec-kit build-out). The
following files are in place already; the rest land in Slices B–E.

| Path | Contains | Slice |
|------|----------|-------|
| `__init__.py` | package marker | A |
| `sql/00_schema_overlay.sql` | `audit_log` + `country_overlay` tables | A |
| `sql/01_role.sql` | `example_user` role + grants | A |
| `sql/10_procedures.sql` | all 10 procedures (pending dump column adaptation) | A |
| `transport.py` | `TransportConfig` dataclass + helpers | A |
| `loader.py` | `LoaderState`, SHA-256 check, fresh/warm/inconsistent detection | A |
| `data/sample_db.sql` + `.sha256` + `UPSTREAM_LICENSE` | vendored dump | B |
| `node/pglite_bridge.js` | PGlite ↔ TCP + pipe bridge | C |
| `launcher.py` | spawn bridge, wait for readiness | C |
| `procedures.py` | idempotent install of the 10 procedures | C |
| `run_example.py` | CLI entry point | C |

Until Slices B–C land, `transport.py` and `loader.py` can be imported
and unit-tested (they are pure stdlib), but there is no working
end-to-end example yet.

## Spec-kit references

The authoritative contract lives at the outer repo root under
[`specs/001-example-db-psycopg3-windows/`](../../../specs/001-example-db-psycopg3-windows/):

- [`spec.md`](../../../specs/001-example-db-psycopg3-windows/spec.md) — user stories, FRs, success criteria
- [`plan.md`](../../../specs/001-example-db-psycopg3-windows/plan.md) — technical approach
- [`contracts/cli.md`](../../../specs/001-example-db-psycopg3-windows/contracts/cli.md) — CLI flags & exit codes
- [`contracts/procedures.md`](../../../specs/001-example-db-psycopg3-windows/contracts/procedures.md) — signatures & SQLSTATEs
- [`contracts/transport.md`](../../../specs/001-example-db-psycopg3-windows/contracts/transport.md) — TCP + named-pipe contract
- [`quickstart.md`](../../../specs/001-example-db-psycopg3-windows/quickstart.md) — 10-minute walk-through
- [`tasks.md`](../../../specs/001-example-db-psycopg3-windows/tasks.md) — 46-task breakdown

## Quick start (after Slices B–C land)

See the spec-kit `quickstart.md` linked above for the authoritative
getting-started instructions. The short version:

```powershell
uv sync --extra windows-sample-db
npm install
python -m examples.windows_sample_db.run_example                # TCP
python -m examples.windows_sample_db.run_example --transport pipe
pytest tests/windows_sample_db -v
```

## Notes

- **Windows only.** Non-Windows runs exit with code 2; the test suite
  skips every test with reason `"requires Windows"`. See FR-011, FR-012.
- The upstream sample rows are **immutable** — mutation procedures write
  to the example-owned `audit_log` and `country_overlay` tables, never
  to `country`/`airport`/etc.
- Both transports authenticate as the single trust-auth role
  `example_user`. No password, no SSPI, no domain account.
