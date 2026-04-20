# Feature Specification: Windows Sample Database Example with TCP and Named Pipe Transports

**Feature Branch**: `001-example-db-psycopg3-windows`
**Created**: 2026-04-20
**Status**: Draft
**Input**: User description: "create an example db and psycopg3 example with data from https://github.com/JannikArndt/PostgreSQLSampleDatabase and 10 sql stored procedures and a full set of tests in windows only for connecting with tcp and also modern pipes and storing the data to disk"

## Clarifications

### Session 2026-04-20

- Q: What authentication strategy should the client use for both TCP and named-pipe connections? → A: Trust-based local auth with a single pre-created local role `example_user`, identical config on both transports; no password.
- Q: How specific should the 10 stored procedures be in the spec? → A: Pin their names plus a one-line purpose in the spec (see "Stored Procedure Catalog" subsection); argument types, return shapes, and error contracts are finalized in `/speckit.plan`.
- Q: How is the Windows named-pipe name derived? → A: Stable default name for normal runs; a `--unique-pipe` flag (and the test suite by default) produce per-process unique names so parallel runs do not collide.
- Q: How is the PostgreSQLSampleDatabase dump supplied — downloaded or vendored? → A: Vendor a copy of the upstream dump inside this repo (with upstream attribution and LICENSE preserved). No network access is required on first run; the downloader path is not implemented.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run the sample database example over TCP on Windows (Priority: P1)

A developer evaluating the project on Windows clones the repo, runs a single command, and sees a working end-to-end example: a PostgreSQL-compatible database is started with a realistic dataset loaded from the PostgreSQLSampleDatabase (Jannik Arndt) reference dump, a Python client connects over TCP (localhost), executes queries and stored procedures, and prints results. The database files are persisted to a known on-disk location so the developer can restart the example without reloading data.

**Why this priority**: This is the minimum viable demonstration. TCP on localhost is the most common, best-supported transport on Windows and is the baseline for anyone new to the project. Without it, nothing else in the feature has value.

**Independent Test**: Fresh clone on a Windows machine → run the example entry point → observe (a) sample tables populated, (b) each of the 10 stored procedures invoked at least once with visible output, (c) data files present under the configured on-disk path. No pipe transport needed.

**Acceptance Scenarios**:

1. **Given** a clean Windows environment with no prior database files, **When** the developer runs the TCP example entry point, **Then** the sample schema and reference data are loaded, all 10 stored procedures are installed, each one is invoked successfully, and output is printed to the console.
2. **Given** the TCP example has been run once and data is already on disk, **When** the developer runs the entry point a second time, **Then** the existing on-disk data is reused (no re-load) and the same stored procedures return consistent results.
3. **Given** the TCP example is running, **When** a standard PostgreSQL-compatible client connects to the advertised host/port, **Then** it can list the sample tables and call the stored procedures by name.

---

### User Story 2 - Run the same example over a modern named-pipe transport on Windows (Priority: P2)

The same developer re-runs the example selecting the named-pipe transport instead of TCP. The example connects to the database using Windows' modern named-pipe IPC (the equivalent of Unix domain sockets on Windows), executes the same queries and stored procedures, and produces equivalent results — demonstrating that the application layer is transport-agnostic.

**Why this priority**: Named-pipe IPC is a meaningful win for local-only workloads (no TCP stack, no loopback firewall prompts, lower latency), but it is an enhancement on top of the TCP baseline. A broken pipe transport should not block adoption of the TCP path.

**Independent Test**: Switch a configuration flag/argument from `tcp` to `pipe` → run the same example → observe identical query and stored-procedure output → confirm no TCP port was opened for the database during the run.

**Acceptance Scenarios**:

1. **Given** the example is configured for the named-pipe transport, **When** the client connects, **Then** the connection succeeds without any TCP listener being bound for the database.
2. **Given** a named-pipe connection is established, **When** each of the 10 stored procedures is invoked, **Then** every procedure returns the same row counts and values as it does over TCP.
3. **Given** the named-pipe transport is requested but not supported in the current Windows environment (e.g., insufficient privileges, legacy OS version), **When** the example starts, **Then** it fails fast with a clear human-readable message stating why the pipe transport is unavailable and how to fall back to TCP.

---

### User Story 3 - Verify behavior with an automated Windows-only test suite (Priority: P2)

A contributor runs the project's test suite on Windows. The suite exercises both transports (TCP and named pipe), loads the sample dataset into a temporary on-disk location, validates schema, data shape, and every stored procedure's contract (inputs, outputs, error behavior), and cleans up after itself. The suite is gated so it only runs on Windows and is skipped with an informative reason on other operating systems.

**Why this priority**: Without automated tests the example drifts into rot. These tests are what turn a demo into a durable reference. P2 because the demo itself (P1) must exist before tests have something to exercise.

**Independent Test**: Run `pytest` (or equivalent) on a Windows machine → all tests pass → run the same command on Linux/macOS → the entire suite is skipped with a clear "Windows-only" reason, not reported as failures.

**Acceptance Scenarios**:

1. **Given** a Windows CI runner, **When** the full suite executes, **Then** both the TCP-transport tests and the named-pipe-transport tests run and report individual pass/fail status.
2. **Given** the test suite has finished, **When** the environment is inspected, **Then** any temporary on-disk database files created for the suite have been removed (or placed in a clearly marked temp directory).
3. **Given** a non-Windows runner, **When** the suite is invoked, **Then** every test in this feature is skipped with a "requires Windows" reason and exit code is 0 (success, nothing ran).
4. **Given** a stored procedure is called with invalid arguments, **When** the test asserts on the error, **Then** the procedure raises a documented, catchable error that the Python client receives intact.

### Edge Cases

- The on-disk data directory already exists and is partially populated (interrupted earlier run) — the example must either resume cleanly or report exactly what is inconsistent and what the developer should delete.
- The configured TCP port is already in use — the example must fail with a clear message naming the port, not hang.
- The stable named-pipe name collides with an existing pipe from a prior crashed run — the example must detect this, report it, suggest `--unique-pipe`, and not silently connect to an unrelated pipe.
- The vendored PostgreSQLSampleDatabase dump file is missing or has been tampered with (wrong size, truncated, modified after commit) — the example must detect the mismatch, refuse to load, and report which file is missing or corrupted.
- A stored procedure is invoked with argument types the sample schema does not accept — the client must receive a typed error, not a silent miscast.
- The data directory is on a path with spaces or non-ASCII characters (common on Windows user profiles) — both transports must still work.
- Concurrent invocation: two test processes try to use the same on-disk data directory — the second must either queue, fail fast with a lock message, or use an isolated temp directory; it must never corrupt the first process's data.

## Requirements *(mandatory)*

### Functional Requirements

**Sample data**

- **FR-001**: The example MUST load the schema and reference data from a vendored copy of the PostgreSQLSampleDatabase dump (Jannik Arndt, https://github.com/JannikArndt/PostgreSQLSampleDatabase) committed inside this repo, preserving the upstream attribution and LICENSE. No network access MUST be required on first run.
- **FR-002**: The example MUST persist the loaded data to a developer-visible location on local disk so that subsequent runs reuse the existing data instead of reloading.
- **FR-003**: The example MUST provide a documented way to reset (delete and reload) the on-disk data without requiring the developer to manually inspect internal files.

**Stored procedures**

- **FR-004**: The example MUST define and install exactly the 10 stored procedures listed in the "Stored Procedure Catalog" subsection below, covering parameterized lookups, aggregations, paginated searches, reporting, bulk and single-row mutations, and intentional error handling.
- **FR-005**: Each stored procedure MUST be callable from the Python client, accept typed arguments, and return results that the client can consume as rows or scalar values.
- **FR-006**: Each stored procedure MUST have a documented contract (name, arguments, return shape, error conditions) that tests can assert against.
- **FR-023**: Stored-procedure mutations MUST NOT alter rows imported from the upstream PostgreSQLSampleDatabase dump; they MUST write to example-owned side tables (`audit_log`, `country_overlay`) so that the reference dataset remains byte-identical across runs.

#### Stored Procedure Catalog

The example installs exactly these 10 procedures. Exact argument types, return shapes, and error codes are finalized during `/speckit.plan`; the names and one-line purposes below are the spec-level contract.

1. **`get_country_by_iso`** — Return the single country row matching a given ISO code; error if not found.
2. **`list_airports_in_country`** — Return a paginated list of airports in a country (inputs: country ISO, page size, page number).
3. **`count_airports_per_country`** — Aggregation: one row per country with its total airport count.
4. **`top_countries_by_population`** — Return the top N countries ordered by population descending (input: N).
5. **`list_neighbors`** — Return the set of countries that share a border with the given country.
6. **`languages_spoken_in`** — Return the languages associated with a given country.
7. **`country_profile_report`** — Reporting routine: return a single consolidated row combining country facts, neighbor count, language count, and airport count.
8. **`bulk_log_airports_visited`** — Bulk mutation: insert one row per airport in the country into the example-owned `audit_log` table; returns the number of rows inserted.
9. **`rename_country_common_name`** — Single-row mutation: upsert a replacement display name for a country into the example-owned `country_overlay` table (does not mutate the upstream countries table).
10. **`assert_country_exists`** — Intentional error demonstrator: raises a typed, catchable error if the given country is not present; returns normally otherwise.

**Transports**

- **FR-007**: The example MUST support connecting from the Python client to the database over TCP on localhost.
- **FR-008**: The example MUST support connecting from the Python client to the database over a modern Windows named-pipe transport (Windows' equivalent of Unix-domain sockets), with no TCP listener required for that run.
- **FR-009**: The transport MUST be selectable at run time via a single clearly-named parameter (command-line flag or environment variable) so the same example code exercises both paths.
- **FR-010**: When the requested transport is unavailable on the current machine, the example MUST fail fast with a human-readable message stating which transport was requested, why it could not be used, and how to switch to the other transport.

**Authentication**

- **FR-020**: Both transports MUST use trust-based local authentication: a single pre-created local role named `example_user` with no password. The same role and the same connection credentials MUST apply to both the TCP path and the named-pipe path.
- **FR-021**: The database MUST reject connections from any role other than `example_user` so that misconfigured clients fail loudly rather than silently binding to another identity.
- **FR-022**: No password, keytab, or Windows domain account MUST be required to run the example or the test suite on a developer workstation.

**Named-pipe naming**

- **FR-024**: The named-pipe transport MUST default to a stable, documented pipe name (so a developer or external client can reconnect across runs without reading a log to discover the name).
- **FR-025**: The example MUST expose a `--unique-pipe` option (and the test suite MUST set it by default) that derives a per-process unique pipe name, so parallel or retried runs do not collide on the stable name.
- **FR-026**: When the stable pipe name is already in use by another process, the example MUST detect the collision and fail with a message that names the conflicting pipe and suggests rerunning with `--unique-pipe`.

**Platform scope**

- **FR-011**: The example and its test suite MUST target Windows only; they MAY be skipped or refuse to run on non-Windows platforms, but they MUST NOT report false failures on those platforms.
- **FR-012**: The test suite MUST detect the host operating system and emit a clear "Windows-only" skip reason on Linux/macOS rather than failing.

**Testing**

- **FR-013**: The test suite MUST exercise both the TCP transport and the named-pipe transport end-to-end, including at least one invocation of every one of the 10 stored procedures over each transport.
- **FR-014**: The test suite MUST verify that loaded sample data matches expected row counts and a small set of spot-check values from the PostgreSQLSampleDatabase reference.
- **FR-015**: The test suite MUST verify that on-disk data produced by a run survives process exit and is visible to a subsequent process.
- **FR-016**: The test suite MUST clean up any ephemeral data directories it creates during the run.
- **FR-017**: The test suite MUST cover at least one negative case per stored procedure (invalid argument, missing row, constraint violation, etc.) and assert that a meaningful error reaches the Python client.

**Developer experience**

- **FR-018**: The feature MUST ship with a short, runnable "getting started" instruction set that takes a new developer on Windows from clone to a successful TCP run in under 10 minutes.
- **FR-019**: The example MUST log which transport it connected over, which on-disk data directory it used, and which of the 10 stored procedures it invoked, so that a support channel reading the output can reconstruct what happened.

### Key Entities *(include if feature involves data)*

- **Sample dataset**: The schema and reference rows imported from a vendored copy of the PostgreSQLSampleDatabase dump committed in this repo. Represents the realistic business data the example queries against. Relationships and row counts are defined by the upstream dump and are expected to be identical across machines.
- **On-disk database directory**: A persistent location under the example's working area that holds the database's data files between runs. Contains all loaded sample data plus any state written by the stored procedures.
- **Stored procedure catalog**: The set of 10 named procedures installed against the sample schema, each with a documented contract (inputs, outputs, error conditions). Enumerated by name in the "Stored Procedure Catalog" subsection. Lives inside the database; its definition scripts live alongside the example code.
- **`audit_log` table (example-owned)**: Append-only log table written by `bulk_log_airports_visited`. Lets mutation procedures demonstrate write behavior without touching upstream sample rows.
- **`country_overlay` table (example-owned)**: Key/value overlay mapping an upstream country's ISO code to a replacement display name. Written by `rename_country_common_name`; upstream `countries` rows remain untouched.
- **Transport configuration**: The run-time choice of TCP vs. named-pipe, plus the parameters each needs (host/port for TCP; pipe name for pipes). Single source of truth for how the client reaches the database. Authenticates as the fixed local role `example_user` regardless of transport.
- **Local database role `example_user`**: The single pre-created role every connection authenticates as. Trust-based (no password); not a Windows domain principal. Created at database initialization and recreated on a data-directory reset.
- **Test artifacts**: Per-test-run temporary data directories and logs produced by the Windows-only test suite. Must not leak between runs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a clean Windows machine, a first-time developer can go from `git clone` to seeing all 10 stored procedures invoked successfully over TCP in under 10 minutes, measured without prior project knowledge.
- **SC-002**: Re-running the example on a machine where data already exists on disk starts producing query output in under 10 seconds (no re-load cost), versus first-run load time.
- **SC-003**: 100% of the 10 stored procedures execute successfully over both TCP and the named-pipe transport, with identical row counts and scalar values across transports for the same inputs.
- **SC-004**: The Windows-only test suite passes with 0 failures on a supported Windows version and is 100% skipped (0 failures, 0 errors) on Linux and macOS runners.
- **SC-005**: Negative-path tests cover every one of the 10 stored procedures — at least 10 error-path assertions in total — and every assertion verifies that a typed, catchable error reached the Python client.
- **SC-006**: When a requested transport cannot be used, 100% of failure messages name the transport requested, the reason it could not be used, and the fallback transport available.
- **SC-007**: After the test suite completes, 0 stray temporary data directories remain outside the declared temp area.

## Assumptions

- "psychog3" in the original request refers to **psycopg 3**, the Python driver for PostgreSQL; this is the client library used for the Python example and tests.
- "modern pipes" refers to the Windows named-pipe transport that recent PostgreSQL-compatible servers and clients support as the Windows analogue of Unix-domain sockets — not legacy anonymous pipes.
- The example will reuse the existing `pglite-pydb` project's embedded PostgreSQL-compatible engine for on-disk persistence; it does not require an externally installed PostgreSQL server.
- The PostgreSQLSampleDatabase dump is redistributable for this purpose. A copy is vendored directly into this repository, with the upstream LICENSE and attribution preserved alongside it.
- The on-disk data directory lives under the example's folder (e.g., `examples/<this-feature>/data/`) by default, and is configurable via a run-time parameter.
- Supported Windows target is a currently-supported release of Windows 10 or 11 (64-bit) with the project's existing Python version requirement; older Windows versions are out of scope.
- The feature adds a new example under `examples/` and new tests under `tests/`; it does not modify the public API of the host project.
- Exactly 10 stored procedures is the contract; the set will be chosen to cover a representative mix (parameterized SELECTs, aggregations, paginated search, bulk mutation, a reporting routine, and at least one deliberately error-raising procedure) against the sample schema.
- "Windows only" means both the example and its test suite — non-Windows platforms are explicitly out of scope for this feature and the tests will skip rather than fail there.
