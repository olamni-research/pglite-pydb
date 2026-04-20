# CLI Contract: `run_example.py`

**Feature**: `001-example-db-psycopg3-windows`
**Module**: `examples/windows_sample_db/run_example.py`

## Invocation

```text
python -m examples.windows_sample_db.run_example [OPTIONS]
```

The module is invokable both as a script (when run from the repo root) and as a `python -m` target.

## Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--transport {tcp,pipe}` | enum | `tcp` | Selects which listener the client connects over (spec FR-009). Falls back to `$PGLITE_EXAMPLE_TRANSPORT` when the flag is omitted (FR-009's env-var branch). |
| `--host` | str | `127.0.0.1` | TCP host. Ignored when `--transport pipe`. |
| `--port` | int | `54320` | TCP port. Ignored when `--transport pipe`. |
| `--pipe-name` | str | `pglite_example` | Named-pipe basename (full path is `\\.\pipe\<pipe-name>`). Ignored when `--transport tcp`. |
| `--unique-pipe` | flag | `false` | When set, suffixes the pipe name with `_<pid>_<uuid8>` to avoid collisions (FR-025). |
| `--data-dir` | path | `examples/windows_sample_db/data/pgdata` (repo-relative) | Absolute or relative path to the PGlite on-disk data directory (FR-002). |
| `--reset` | flag | `false` | Wipes `--data-dir` before starting, forces a fresh load (FR-003). |
| `--log-level` | enum | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `--help` / `-h` | flag | — | Prints this contract. |

## Behavior

On startup the example:
1. Validates the requested transport is available on this platform; on non-Windows, exits immediately with code `2` and message `"requires Windows"` (FR-011).
2. Verifies the vendored dump's SHA-256 matches `sample_db.sql.sha256`. On mismatch, exits with code `3` and names the file (spec edge case).
3. Starts the PGlite Node bridge (`node/pglite_bridge.js`) with the requested data dir, TCP listener, and pipe listener. Waits until both listeners are accepting connections.
4. If the data directory is empty or marked fresh, runs the dump loader and installs the 10 procedures.
5. Opens a psycopg 3 connection over the chosen transport as role `example_user` (no password, trust auth — FR-020).
6. Invokes each of the 10 procedures once with representative inputs; prints name, inputs, row count / scalar result, and elapsed time to stdout.
7. Exits cleanly with code `0`, closing the connection and stopping the bridge.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success — all 10 procedures invoked, output printed. |
| `1` | Unexpected runtime error (caught exception); details on stderr. |
| `2` | Platform not supported (non-Windows). |
| `3` | Vendored dump integrity check failed. |
| `4` | Requested transport unavailable (e.g. pipe creation refused by OS); message names transport + fallback (FR-010). |
| `5` | Port or pipe name already in use (FR-026). |
| `6` | Data directory inconsistent (has `postmaster.opts` but no `PG_VERSION` — interrupted prior run); message suggests `--reset`. |

Every failure message MUST include (a) which transport was requested, (b) what went wrong, and (c) a suggested fix.

## Logging contract (FR-019)

At `INFO` level the example emits exactly these log lines, in order, for every run:

```text
[example] transport=<tcp|pipe> host=<h> port=<p> pipe=<name-or-"-"> data_dir=<abs-path>
[example] dump=ok sha256=<hex>... (<bytes> bytes)
[example] pgdata status=<fresh|warm>
[example] procedures installed=<10 of 10> (skipped=<N if warm>)
[example] connected as role=example_user transport=<tcp|pipe>
[example] proc=<name> rows=<n> elapsed_ms=<float>   (× 10 — one per procedure)
[example] done exit=0
```

A support channel reading this output can reconstruct what ran and which transport was used.
