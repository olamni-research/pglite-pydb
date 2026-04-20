# Phase 0 Research: Windows Sample Database Example

**Feature**: `001-example-db-psycopg3-windows`
**Date**: 2026-04-20
**Purpose**: Resolve unknowns from the Technical Context before design.

---

## R1 — PGlite transport options on Windows: what do "TCP" and "modern pipe" resolve to concretely?

**Decision**: Use a thin Node-side bridge (`pglite_bridge.js`) that wraps `@electric-sql/pglite` and opens **exactly one** Postgres-wire-protocol listener per process, chosen via a required `--transport {tcp|pipe}` CLI argument to the bridge:
- `--transport tcp` → `net.createServer().listen(port, '127.0.0.1')` only. **No named pipe is bound.**
- `--transport pipe` → `net.createServer().listen('\\\\.\\pipe\\<name>')` only. **No TCP port is bound.**

The existing `py_pglite.manager.PGliteManager` already spawns a Node child process and speaks Postgres wire protocol over a Unix-domain-socket path (`config.socket_path`). For this feature we keep that manager's lifecycle primitives (spawn, wait-for-ready, cleanup) but point it at our own bridge script. The Python client connects to whichever transport the current bridge process bound; for tests that need both, the fixture runs two bridges (R11).

This enforces spec FR-008 ("with no TCP listener required for that run") literally: a `--transport pipe` process has no TCP socket in its fd table, observable externally via `Get-NetTCPConnection -LocalPort <port>` returning nothing.

**Rationale**:
- PGlite does not expose a TCP listener natively — it is an in-process WASM PostgreSQL. Every "remote" access goes through a JS-side socket bridge. The `@electric-sql/pglite-socket` package (or the bundled `pglite-server` pattern) already implements such a bridge; extending it to bind both TCP and a named pipe is ~50 lines of Node.
- Node's `net.createServer().listen(path)` on Windows, when `path` starts with `\\.\pipe\`, creates a Windows named pipe in kernel — no `pywin32` is needed on the server side.
- Postgres wire protocol is transport-agnostic (it only requires a byte-stream). psycopg 3 speaks it unchanged over either a TCP socket or a named-pipe byte stream given the right connection string.

**Alternatives considered**:
- *AF_UNIX socket on Windows 10 1803+*: Windows supports AF_UNIX natively, but psycopg 3 on Windows does not expose a `host=<file-path>` Unix-socket mode (it resolves `host` as a TCP hostname on Windows). We would have to fork psycopg's connection dialer. Rejected as too invasive for an example.
- *Legacy anonymous pipes*: spec explicitly asks for "modern pipes"; anonymous pipes can't be listened on by name and are out.
- *Run full PostgreSQL instead of PGlite*: duplicates a system dependency the host project explicitly avoids.
- *HTTP tunnel*: out of scope; the spec's success criteria are about real Postgres-wire transport.

## R2 — Named-pipe name resolution on the psycopg 3 side (Windows)

**Decision**: psycopg 3 on Windows accepts **TCP only** via its connection-string parser. To connect psycopg to the named pipe, we use a small Python-side adapter in `transport.py` that:
1. Opens the pipe as a file handle via `win32file.CreateFile(r"\\.\pipe\<name>", ...)`.
2. Wraps the handle in a `socket`-like object using `pywin32`'s `win32pipe` helpers and a custom `io.RawIOBase` shim.
3. Passes the wrapped object to psycopg as a pre-connected stream via `psycopg.Connection.connect(..., conninfo="", factory=...)` using psycopg's `autodetect` + "connection from an already-open socket" pattern (psycopg 3 exposes this via `pgconn` low-level + `Connection.wrap()`).

If the socket-wrap path turns out to not work cleanly across psycopg versions, the fallback is to run a 1-connection-per-spawn Python relay that proxies the pipe to a loopback TCP port (transparent to the test assertions). The relay adds ~0.2ms per round trip — acceptable for an example.

**Rationale**:
- Keeping psycopg's Postgres protocol handling untouched (no forking libpq) is important; the spec says "connect with psycopg" — we must.
- `pywin32` is the standard, stable way to talk Windows named pipes from Python and is MIT-licensed.

**Alternatives considered**:
- *libpq's own named-pipe support*: libpq does not speak Windows named pipes in any released build as of this plan's date; this would require a custom libpq build. Rejected.
- *Rely on AF_UNIX*: see R1 — psycopg on Windows does not route `host=<path>` to AF_UNIX.

**Risk**: If psycopg's `Connection.wrap()` API does not accept a non-socket stream in the shipping version used by the project, we fall back to the in-process relay (above). Both paths keep the public Python API identical.

## R3 — Does PGlite support `CREATE PROCEDURE` / `CREATE FUNCTION` with PL/pgSQL?

**Decision**: Yes — PGlite ships with PL/pgSQL enabled by default and supports `CREATE PROCEDURE`, `CREATE FUNCTION`, `CALL`, and `RAISE EXCEPTION`. All 10 spec procedures can be authored in standard PL/pgSQL.

**Rationale**: `@electric-sql/pglite` bundles PostgreSQL 16.x compiled to WASM with the standard PL/pgSQL extension pre-loaded. `CREATE PROCEDURE` (introduced in Postgres 11) is therefore available. Limitation to be aware of: PGlite does not support `LISTEN/NOTIFY` fanout across processes, and some background workers are stubs — neither is required by the 10 procedures in our catalog.

**Alternatives considered**:
- *SQL-only functions*: would work for 8 of the 10 but not for `assert_country_exists` (needs `RAISE`) or `bulk_log_airports_visited` (needs a loop with side effects). Rejected — PL/pgSQL covers all 10 uniformly.

## R4 — PGlite on-disk persistence

**Decision**: Pass `dataDir: <absolute path>` to the PGlite constructor in `pglite_bridge.js`. PGlite persists to this directory on every commit; reopening with the same path resumes state. Default path is `examples/windows_sample_db/data/pgdata/`; override via `--data-dir <abs>` CLI flag.

**Rationale**: PGlite's `dataDir` option is the officially-supported persistence mechanism. It writes a directory tree compatible across PGlite minor versions on the same major.

**Alternatives considered**:
- *`idb://...` (IndexedDB)*: browser-only, not applicable.
- *`memory://`*: loses state between runs — violates FR-002.
- *Export/import via SQL dump between runs*: slow and would re-trigger FR-001 load costs on every run — violates SC-002.

**Edge cases handled**:
- `pgdata/` exists but is incomplete (interrupted earlier run) → loader detects missing `PG_VERSION` file and refuses to start; documented reset command wipes `pgdata/` and re-loads.
- Path contains spaces or non-ASCII → absolute path is passed unquoted through a properly-escaped argv to Node; tested explicitly in `test_loader.py`.

## R5 — Vendoring the JannikArndt sample dump

**Decision**: Commit a single `sample_db.sql` file containing the full upstream dump into `examples/windows_sample_db/data/`, plus its SHA-256 checksum and the upstream `LICENSE` + an attribution note. First-run load executes the file via `psycopg`'s `copy`/`execute` against the freshly-initialized PGlite instance as the `example_user` role.

Source: the upstream repo is https://github.com/JannikArndt/PostgreSQLSampleDatabase — permissively licensed (see its LICENSE). We pin to a specific commit SHA in `UPSTREAM_ATTRIBUTION.md`.

**Integrity**: The loader computes SHA-256 of `sample_db.sql` at run time and compares to `sample_db.sql.sha256`. A mismatch aborts the load (spec edge case "dump file tampered").

**Size expectation**: the upstream dump is well under 10 MB — acceptable to commit. If it turns out to be larger than 25 MB at implementation time, switch to `sample_db.sql.gz` and decompress on load (still offline).

**Alternatives considered**:
- *Download at setup*: rejected per spec clarification (Q4 → A: vendor).
- *`pg_dump` custom format*: requires a pg_restore binary at load time, which PGlite does not ship. SQL text dumps are universal. Rejected.

## R6 — Trust-based auth on PGlite

**Decision**: PGlite does not enforce `pg_hba.conf` in the standard way (there is no host-based configuration — every connection via the bridge is effectively trusted). We implement trust-auth as a Python-layer contract:
1. On PGlite startup, the bridge creates a single role `example_user` with no password and grants it schema/USAGE + table SELECT/INSERT/UPDATE on the sample schema + `audit_log` + `country_overlay`.
2. The bridge accepts any incoming startup packet where `user = example_user` and forwards it to PGlite; it rejects (with a clear error message) startup packets where `user != example_user` (FR-021). This check lives in the Node bridge, not in PGlite.

**Rationale**: PGlite's WASM build is single-user internally; the Node bridge is the only choke point where we can enforce the role constraint required by FR-021.

**Alternatives considered**:
- *Enforce the role inside PGlite via `pg_hba.conf`*: PGlite does not parse `pg_hba.conf`. Rejected.
- *Accept any role*: violates FR-021 ("reject connections from any role other than `example_user`"). Rejected.

## R7 — Pytest Windows-only skip strategy

**Decision**: Add a package-level `conftest.py` at `tests/windows_sample_db/conftest.py` that contains:

```python
import sys, pytest
collect_ignore_glob = [] if sys.platform == "win32" else ["test_*.py"]
```

Combined with a per-module `pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")` as belt-and-braces, this guarantees:
- On Linux/macOS: every test in this folder is collected-but-skipped *or* entirely excluded from collection (exit code 0, zero failures — spec SC-004).
- On Windows: all tests run.

**Rationale**: `collect_ignore_glob` is stdlib pytest and produces the cleanest "nothing ran" output on non-Windows runners. `skipif` provides a second layer so direct pytest invocations (e.g. `pytest tests/windows_sample_db/test_loader.py` on Linux) still skip cleanly rather than importing Windows-only modules.

## R8 — Transport-matrix fixture

**Decision**: A `@pytest.fixture(params=["tcp", "pipe"], ids=["tcp", "pipe"])` at the `conftest.py` level yields a connected `psycopg.Connection` pre-configured for each transport. The fixture:
- Starts a fresh PGlite bridge per test session (session-scoped manager fixture).
- Creates the transport connection per test (function-scoped to avoid state bleed).
- For `pipe`, sets `--unique-pipe` so parallel workers never collide (FR-025).

**Rationale**: Spec SC-003 requires identical results across transports. A single parametrized fixture is the idiomatic pytest way to express that every test must pass on both without duplicating assertions.

## R9 — `--unique-pipe` derivation

**Decision**: Unique pipe name = `pglite_example_{os.getpid()}_{uuid.uuid4().hex[:8]}`. Deterministic enough to log, unique enough to avoid collisions with both crashed prior runs and concurrent test workers.

**Rationale**: PID alone isn't enough (`pytest-xdist` workers can share a PID in some reattach scenarios); PID + short UUID4 is cheap and reliable.

## R10 — Sample data row-count spot-checks for tests

**Decision**: The test suite asserts on a small set of stable spot-check values pulled from the upstream README + dump (e.g. "USA exists", "Germany has 16 federal states in the dump", "number of countries == N"). Exact numbers are recorded in `tests/windows_sample_db/_expected.py` and reviewed when the upstream pin changes.

**Rationale**: Checksum alone proves the *file* hasn't changed, but we also want confidence the *data loaded correctly*. Row-count and a few lookups give that without coupling the tests to every row.

---

## Summary of resolved unknowns

| Unknown in Technical Context | Resolution |
|------------------------------|-----------|
| How to bind PGlite to TCP on Windows | Node bridge with `net.createServer().listen(port, '127.0.0.1')` (R1) |
| What "modern pipe" means concretely | Windows named pipe `\\.\pipe\<name>` via Node + pywin32 (R1, R2) |
| psycopg 3 → named-pipe compatibility | pywin32 pipe wrap + `Connection.wrap()`; fallback is in-process TCP relay (R2) |
| PGlite stored-procedure support | PL/pgSQL + `CREATE PROCEDURE` both supported (R3) |
| On-disk persistence | PGlite `dataDir` option (R4) |
| How to vendor the dump | Single SQL text dump + SHA-256 + LICENSE + attribution (R5) |
| Enforcing `example_user`-only auth on PGlite | Bridge-layer role check (R6) |
| Non-Windows skip behavior | `collect_ignore_glob` + `skipif` (R7) |
| Transport matrix in tests | `pytest.fixture(params=[...])` (R8) |
| Unique-pipe naming | `pid + uuid4[:8]` (R9) |
| Spot-check data fidelity after load | Pinned values in `_expected.py` (R10) |

All NEEDS CLARIFICATION from plan.md Technical Context are resolved. Proceeding to Phase 1.

---

## R11 — Two bridges for matrix tests sharing one data directory

**Decision**: The session-scoped pytest fixture spawns **two** Node bridge processes sharing the same `dataDir`:
- `bridge_tcp` → listens on `127.0.0.1:54320`.
- `bridge_pipe` → listens on `\\.\pipe\pglite_example_<pid>_<uuid8>`.

The parametrized function-scoped `transport_conn` fixture returns a psycopg 3 connection to whichever bridge matches the current parameter.

**Rationale**: PGlite's on-disk format permits multiple processes reading the same directory, but concurrent writers must not overlap. Our test suite avoids this by:
1. Running tests with `pytest -n 1` (the project's existing default — see `pyproject.toml` `addopts = ["-n", "1"]`).
2. Both bridges open PGlite with the same `dataDir` but never write concurrently — only one fixture's connection is active per test.

If a future change enables `pytest-xdist` parallelism, each worker must isolate to its own temp `dataDir` (already the `test_cleanup.py` pattern for ephemeral-dir tests).

**Alternatives considered**:
- *One bridge with both listeners* (original R1 wording): violates FR-008; rejected (see F1 in analysis report 2026-04-20).
- *Restart a single bridge between each test to flip transport*: 2–5s startup cost × every test ≈ 40+s per suite. Rejected — unacceptable for CI loop.
- *Run two separate pytest invocations, one per transport*: doubles the outer fixture cost and loses parametrized reporting. Rejected.
