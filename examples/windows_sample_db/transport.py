"""Transport configuration for the Windows sample-database example.

Pure-stdlib module; deliberately has no ``psycopg``/``pywin32`` imports at
module scope so it can be constructed even on non-Windows platforms for
testing / introspection. The live connection path (Slice B/C/D) imports
them locally inside the functions that actually need them.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Literal


TransportKind = Literal["tcp", "pipe"]

DEFAULT_TCP_HOST = "127.0.0.1"
DEFAULT_TCP_PORT = 54320          # chosen to avoid the stock PostgreSQL 5432
DEFAULT_PIPE_NAME = "pglite_example"
DEFAULT_ROLE = "example_user"
DEFAULT_DBNAME = "postgres"


def default_data_dir() -> Path:
    """Absolute default path for the PGlite on-disk data directory.

    Resolves to ``<this-package>/data/pgdata/`` so the default is stable
    across working directories.
    """
    return (Path(__file__).resolve().parent / "data" / "pgdata").resolve()


def unique_pipe_name(stem: str = DEFAULT_PIPE_NAME) -> str:
    """Derive a per-process pipe name: ``<stem>_<pid>_<uuid8>``.

    Used by ``--unique-pipe`` and by the pytest fixture (research R9).
    """
    return f"{stem}_{os.getpid()}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class TransportConfig:
    """Run-time transport choice and all its parameters.

    Validated at construction; ``kind`` selects which fields apply.
    """

    kind: TransportKind
    host: str = DEFAULT_TCP_HOST
    port: int = DEFAULT_TCP_PORT
    pipe_name: str = DEFAULT_PIPE_NAME
    unique_pipe: bool = False
    data_dir: Path = field(default_factory=default_data_dir)
    _resolved_pipe_name: str = field(default="", init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.kind not in ("tcp", "pipe"):
            raise ValueError(
                f"transport kind must be 'tcp' or 'pipe', got {self.kind!r}"
            )
        if self.kind == "tcp":
            if not self.host:
                raise ValueError("tcp transport requires a non-empty host")
            if not (1 <= int(self.port) <= 65535):
                raise ValueError(
                    f"tcp transport requires 1<=port<=65535, got {self.port!r}"
                )
        else:  # pipe
            if not self.pipe_name:
                raise ValueError("pipe transport requires a non-empty pipe_name")
            if "\\" in self.pipe_name or "/" in self.pipe_name:
                raise ValueError(
                    "pipe_name must be the bare stem, "
                    f"not a path; got {self.pipe_name!r}"
                )
            # Resolve the pipe name ONCE at construction so every later
            # property access (and every downstream subprocess arg) sees
            # the same string — unique_pipe_name() mints a fresh uuid on
            # every call, which would otherwise desync launcher vs bridge.
            resolved = (
                unique_pipe_name(self.pipe_name) if self.unique_pipe
                else self.pipe_name
            )
            object.__setattr__(self, "_resolved_pipe_name", resolved)
        if not self.data_dir.is_absolute():
            raise ValueError(
                f"data_dir must be absolute; got {self.data_dir!r}"
            )

    # -- derived values ------------------------------------------------------

    @property
    def resolved_pipe_name(self) -> str:
        """The actual pipe stem to use, taking ``unique_pipe`` into account."""
        if self.kind != "pipe":
            raise ValueError("resolved_pipe_name only valid for pipe transport")
        return self._resolved_pipe_name

    @property
    def pipe_path(self) -> str:
        r"""Full Windows named-pipe path: ``\\.\pipe\<name>``."""
        return rf"\\.\pipe\{self.resolved_pipe_name}"

    def to_dsn(
        self,
        *,
        role_override: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> str:
        """psycopg 3 connection string.

        For TCP transports the DSN points straight at ``self.host:self.port``.
        For pipe transports the caller MUST pass ``host``+``port`` pointing
        at the in-process relay's loopback endpoint (psycopg 3 has no
        pre-connected-stream factory, so the adapter in Slice C (T028/T029)
        proxies pipe bytes through an ephemeral TCP port — see research R2).
        """
        role = role_override or DEFAULT_ROLE
        if self.kind == "tcp":
            h = host if host is not None else self.host
            p = port if port is not None else self.port
        else:
            if host is None or port is None:
                raise ValueError(
                    "pipe transport requires host= and port= overrides "
                    "pointing at the active PipeRelay; see launcher.BridgeHandle.dsn()"
                )
            h, p = host, port
        return f"host={h} port={p} user={role} dbname={DEFAULT_DBNAME}"


# ---------------------------------------------------------------------------
# Pipe transport (T028 + T029)
#
# psycopg 3.x doesn't expose a pre-connected-stream factory, so the pipe
# adapter runs an in-process TCP relay: a loopback socket accepts whatever
# psycopg dials, and each incoming socket is paired with a freshly-opened
# pywin32 pipe handle. The two are spliced with a pair of daemon threads
# doing bidirectional byte-shovelling. contracts/transport.md blesses this
# as "invisible to tests: the connection object, server identity, and
# results are identical."
# ---------------------------------------------------------------------------


class PipeUnavailable(RuntimeError):
    """Raised when the named pipe can't be opened for policy / denial reasons.

    Distinct from ``PipeBusy`` (which the launcher maps to exit 5). This
    covers ``CreateFile`` failures like ``ERROR_ACCESS_DENIED`` or
    ``ERROR_PATH_NOT_FOUND`` — legacy OS / AV denial / missing privileges
    (FR-010, spec US2 AS-3). Mapped to CLI exit code 4.
    """

    exit_code: int = 4


class PipeRelay:
    """In-process TCP ↔ named-pipe byte relay.

    Owns one listening loopback socket; spawns one handler thread per
    accepted client that opens a fresh pipe handle and shuttles bytes in
    both directions until either side closes.
    """

    def __init__(self, pipe_path: str) -> None:
        self._pipe_path = pipe_path
        self._srv: socket.socket | None = None
        self._port: int = 0
        self._accept_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> int:
        """Bind the loopback listener and spawn the accept loop. Returns port."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self._srv = srv
        self._port = srv.getsockname()[1]
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name=f"pipe-relay-{self._port}",
            daemon=True,
        )
        self._accept_thread.start()
        return self._port

    def stop(self) -> None:
        self._stop.set()
        if self._srv is not None:
            with contextlib.suppress(OSError):
                self._srv.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2.0)
        for t in list(self._workers):
            t.join(timeout=1.0)

    def _accept_loop(self) -> None:
        assert self._srv is not None
        while not self._stop.is_set():
            try:
                client, _ = self._srv.accept()
            except OSError:
                return
            t = threading.Thread(
                target=self._handle_client,
                args=(client,),
                daemon=True,
                name=f"pipe-relay-worker-{client.fileno()}",
            )
            self._workers.append(t)
            t.start()

    def _handle_client(self, client: socket.socket) -> None:
        """Splice one accepted loopback socket to a freshly-opened pipe handle.

        The pipe is opened with ``FILE_FLAG_OVERLAPPED`` so that the read
        and write threads don't serialize each other on the same handle
        — without overlapped I/O, Windows synchronous I/O on a single
        pipe HANDLE is ordered per-handle, which deadlocks bidirectional
        traffic (a pending ReadFile blocks any concurrent WriteFile from
        a sibling thread until data arrives).
        """
        import win32event
        import win32file
        import pywintypes

        try:
            handle = win32file.CreateFile(
                self._pipe_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_OVERLAPPED,
                None,
            )
        except pywintypes.error:
            with contextlib.suppress(OSError):
                client.close()
            return

        done = threading.Event()

        def _sock_to_pipe() -> None:
            ov = pywintypes.OVERLAPPED()
            ov.hEvent = win32event.CreateEvent(None, True, False, None)
            try:
                while not done.is_set():
                    data = client.recv(8192)
                    if not data:
                        return
                    win32event.ResetEvent(ov.hEvent)
                    win32file.WriteFile(handle, data, ov)
                    win32file.GetOverlappedResult(handle, ov, True)
            except (OSError, pywintypes.error):
                return
            finally:
                with contextlib.suppress(Exception):
                    win32file.CloseHandle(ov.hEvent)
                done.set()

        def _pipe_to_sock() -> None:
            ov = pywintypes.OVERLAPPED()
            ov.hEvent = win32event.CreateEvent(None, True, False, None)
            buf = win32file.AllocateReadBuffer(8192)
            try:
                while not done.is_set():
                    win32event.ResetEvent(ov.hEvent)
                    try:
                        hr, _ = win32file.ReadFile(handle, buf, ov)
                    except pywintypes.error:
                        return
                    n = win32file.GetOverlappedResult(handle, ov, True)
                    if n == 0:
                        return
                    client.sendall(bytes(buf[:n]))
            except (OSError, pywintypes.error):
                return
            finally:
                with contextlib.suppress(Exception):
                    win32file.CloseHandle(ov.hEvent)
                done.set()

        t1 = threading.Thread(target=_sock_to_pipe, daemon=True)
        t2 = threading.Thread(target=_pipe_to_sock, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

        with contextlib.suppress(Exception):
            win32file.CloseHandle(handle)
        with contextlib.suppress(OSError):
            client.close()
