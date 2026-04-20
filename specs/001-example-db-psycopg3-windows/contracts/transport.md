# Transport Contract

**Feature**: `001-example-db-psycopg3-windows`
**Implementer**: `examples/windows_sample_db/transport.py` + `node/pglite_bridge.js`

## Scope

This contract covers how the Python client reaches the PGlite-backed database. The Postgres wire protocol carried over either transport is standard and not re-specified here. The bridge process binds **exactly one** transport at a time, selected by its `--transport` argument; a `--transport pipe` bridge does not open any TCP socket, and vice versa.

---

## Transport 1 — TCP (loopback)

- **Address**: `host` (default `127.0.0.1`) + `port` (default `54320`), bound **only** when the bridge was started with `--transport tcp`.
- **Binding policy**: listener is bound to the loopback interface only — never `0.0.0.0`. No external access is permitted.
- **Startup handshake**: Python side polls the port with a 250 ms retry up to 10 s; fails with exit code `4` on timeout.
- **psycopg 3 connection string**: `"host=127.0.0.1 port=54320 user=example_user dbname=postgres"` — no password, no sslmode.
- **Port collision**: if the port is already in use, startup aborts with exit code `5` and a message naming the port; no listener is inherited.
- **Shutdown**: closed by the bridge on SIGTERM / Ctrl-C and on clean example exit.

## Transport 2 — Windows named pipe

- **Pipe path**: `\\.\pipe\<name>` where `<name>` is `pglite_example` by default, or `pglite_example_<pid>_<uuid8>` if `--unique-pipe` is set. Created **only** when the bridge was started with `--transport pipe`; no TCP socket is opened for this bridge process (externally verifiable with `Get-NetTCPConnection`).
- **Binding policy**: default security descriptor grants access to the creating user and Administrators only; no Everyone ACE.
- **Startup handshake**: Python side polls the pipe with `WaitNamedPipeW(<path>, 250)` up to 10 s; fails with exit code `4` on timeout.
- **psycopg 3 connection**: Python adapter opens the pipe via `win32file.CreateFile`, wraps it with a socket-compatible shim, and passes the handle into psycopg (see research R2). Connection string carries `user=example_user dbname=postgres` only — no host/port.
- **Name collision**: if the stable name is already taken by another pipe, startup aborts with exit code `5` and a message suggesting `--unique-pipe` (FR-026).
- **Fallback path**: if psycopg's pre-connected-stream factory is unavailable in the installed version, the Python adapter spawns an in-process TCP relay bound to an ephemeral loopback port and proxies bytes to the pipe. This fallback is invisible to tests: the connection object, server identity, and results are identical.

## When *either* transport is unavailable

Failure message MUST name the transport requested, why it could not be used, and a suggested fallback (FR-010). Example:

```text
transport 'pipe' unavailable: CreateNamedPipeW failed with ERROR_ACCESS_DENIED (5)
— rerun with --transport tcp to use the TCP listener instead.
```

## Authentication contract (both transports)

- Role: `example_user` only. Any connection that opens with a different role is rejected at the bridge layer with `FATAL: role "<x>" is not permitted on this example server` before the Postgres startup handshake completes (FR-021).
- No password, no SSPI, no TLS. Trust auth only. Every developer workstation runs identically (FR-020, FR-022).
- The role has `LOGIN` and the grants listed in `data-model.md` Layer 2.

## Observability (both transports)

The bridge emits one startup line naming the single transport it bound, plus one line per incoming connection:

```text
[bridge] start transport=<tcp|pipe> listen=<host:port|\\.\pipe\name> data_dir=<abs>
```

```text
[bridge] accept transport=<tcp|pipe> peer=<pid-or-"-"> role=<requested-role> result=<accept|reject>
```

Tests assert on these lines to prove which transport actually carried a given connection (e.g., to verify `--transport pipe` does **not** accept TCP connections in the pipe-only tests).
