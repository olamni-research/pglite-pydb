"""Launcher for the Node-side PGlite bridge (T014).

Spawns ``node/pglite_bridge.js`` as a subprocess, polls the TCP port for
readiness up to 10 s, and distinguishes *port already in use* (abort with
exit code 5) from *not yet up* (keep polling). Tees the bridge's stdout
so that ``[bridge] start …`` / ``[bridge] accept …`` lines surface to the
caller unchanged, optionally forwarding parsed ``accept`` events to a
callback so the CLI can correlate its own log line with what the bridge
observed on the wire.

Pipe transport is not implemented here; ``launch_bridge`` will raise
``NotImplementedError`` if called with ``kind='pipe'`` — that path lands
with T031/T031b in US2.
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import TextIO

from examples.windows_sample_db.transport import PipeRelay
from examples.windows_sample_db.transport import PipeUnavailable
from examples.windows_sample_db.transport import TransportConfig


if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator


READINESS_TIMEOUT_SECS = 10.0
READINESS_POLL_INTERVAL_SECS = 0.25
BRIDGE_JS_REL = Path("node") / "pglite_bridge.js"


class LauncherError(RuntimeError):
    exit_code: int = 1


class PortInUseError(LauncherError):
    """TCP port or pipe name already bound by another process (maps to exit 5)."""
    exit_code = 5


class BridgeStartTimeout(LauncherError):
    """Bridge process never accepted a probe connection within the budget."""
    exit_code = 4


class BridgeSpawnError(LauncherError):
    """Node could not be launched (PATH miss, bad JS path, etc.)."""
    exit_code = 1


class BridgePipeDenied(LauncherError):
    """Bridge's CreateNamedPipeW was denied (ACL / AV / legacy OS)."""
    exit_code = 4


@dataclass
class AcceptEvent:
    transport: str
    peer: str
    role: str
    result: str   # "accept" | "reject"


@dataclass
class BridgeHandle:
    process: subprocess.Popen[str]
    config: TransportConfig
    log_path: Path | None
    _stdout_thread: threading.Thread = field(repr=False)
    _stderr_thread: threading.Thread = field(repr=False)
    _accept_events: list[AcceptEvent] = field(default_factory=list, repr=False)
    relay: PipeRelay | None = field(default=None, repr=False)

    def accept_events(self) -> list[AcceptEvent]:
        return list(self._accept_events)

    def dsn(self, role_override: str | None = None) -> str:
        """psycopg 3 DSN string for whichever transport this bridge is on."""
        if self.config.kind == "tcp":
            return self.config.to_dsn(role_override=role_override)
        assert self.relay is not None, "pipe bridge handle must own a PipeRelay"
        return self.config.to_dsn(
            role_override=role_override,
            host="127.0.0.1",
            port=self.relay.port,
        )

    def terminate(self, timeout: float = 3.0) -> int:
        proc = self.process
        # Tear down the relay first so psycopg sees clean socket closes
        # rather than ECONNRESET from the pipe going away underneath it.
        if self.relay is not None:
            with contextlib.suppress(Exception):
                self.relay.stop()
        if proc.poll() is not None:
            return proc.returncode
        try:
            if sys.platform == "win32":
                # Popen.terminate on Windows is TerminateProcess (SIGKILL-equiv);
                # use send_signal(CTRL_BREAK) first for the Node process group.
                with contextlib.suppress(Exception):
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=timeout)
        finally:
            self._stdout_thread.join(timeout=1.0)
            self._stderr_thread.join(timeout=1.0)
        return proc.returncode


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_bridge_js() -> Path:
    """Return the absolute path to ``node/pglite_bridge.js``."""
    p = (Path(__file__).resolve().parent / BRIDGE_JS_REL).resolve()
    if not p.is_file():
        raise BridgeSpawnError(f"bridge script not found: {p}")
    return p


def _resolve_node_binary() -> str:
    exe = shutil.which("node") or shutil.which("node.exe")
    if not exe:
        raise BridgeSpawnError(
            "`node` not found on PATH; install Node.js >= 20 and retry"
        )
    return exe


def _probe_tcp(host: str, port: int, connect_timeout: float = 0.25) -> str:
    """Single probe. Returns:

    - ``"ready"``   — connection accepted
    - ``"refused"`` — TCP RST/ICMP unreachable (not yet listening)
    - ``"busy"``    — EADDRINUSE observed from a *different* process
                      (only detected via pre-bind check below, not here)
    - ``"timeout"`` — connect_timeout exceeded
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(connect_timeout)
        try:
            s.connect((host, port))
            return "ready"
        except ConnectionRefusedError:
            return "refused"
        except TimeoutError:
            return "timeout"
        except OSError as e:
            # WSAETIMEDOUT = 10060, WSAECONNREFUSED = 10061, WSAEADDRNOTAVAIL = 10049
            if e.errno in (errno.ECONNREFUSED, 10061):
                return "refused"
            if e.errno in (errno.ETIMEDOUT, 10060):
                return "timeout"
            raise


def _port_is_occupied(host: str, port: int) -> bool:
    """Pre-flight check: is this host:port already bound by someone else?

    We attempt to *bind* the port ourselves for a split second; if bind
    fails with EADDRINUSE (or WSAEADDRINUSE 10048), the port is taken.
    The socket is closed immediately regardless so the bridge gets a
    clean shot at the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, 10048):
                return True
            raise
    return False


def _reader_thread(
    stream: TextIO,
    tee: TextIO,
    log_file: TextIO | None,
    events: list[AcceptEvent],
    on_accept: Callable[[AcceptEvent], None] | None,
    started: threading.Event | None = None,
) -> threading.Thread:
    def _run() -> None:
        for line in iter(stream.readline, ""):
            tee.write(line)
            tee.flush()
            if log_file is not None:
                log_file.write(line)
                log_file.flush()
            if started is not None and line.startswith("[bridge] start "):
                started.set()
            evt = _parse_accept_line(line)
            if evt is not None:
                events.append(evt)
                if on_accept is not None:
                    try:
                        on_accept(evt)
                    except Exception:
                        pass
        stream.close()
    t = threading.Thread(target=_run, name=f"bridge-reader-{id(stream):x}", daemon=True)
    t.start()
    return t


def _parse_accept_line(line: str) -> AcceptEvent | None:
    """Parse ``[bridge] accept transport=… peer=… role=… result=…``.

    Returns None on any line that isn't an accept event, including start
    lines, protocol errors, or free-form stderr.
    """
    s = line.strip()
    if not s.startswith("[bridge] accept "):
        return None
    kv: dict[str, str] = {}
    for tok in s[len("[bridge] accept "):].split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            kv[k] = v
    try:
        return AcceptEvent(
            transport=kv["transport"],
            peer=kv.get("peer", "-"),
            role=kv.get("role", ""),
            result=kv["result"],
        )
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _pipe_exists(pipe_path: str) -> bool:
    """Return True if a named-pipe instance is waiting at ``pipe_path``."""
    import pywintypes
    import win32pipe
    try:
        # 0-ms wait: immediate probe — returns True only if already available.
        return bool(win32pipe.WaitNamedPipe(pipe_path, 0))
    except pywintypes.error:
        return False


def launch_bridge(
    config: TransportConfig,
    *,
    log_path: Path | None = None,
    on_accept: Callable[[AcceptEvent], None] | None = None,
    env: dict[str, str] | None = None,
) -> BridgeHandle:
    """Spawn the Node bridge and block until its listener is ready.

    Raises:
        PortInUseError: requested TCP port or pipe name is already bound.
        BridgeStartTimeout: bridge never became ready within
            :data:`READINESS_TIMEOUT_SECS`.
        BridgeSpawnError: Node or the bridge script couldn't be launched.
        BridgePipeDenied: bridge's ``CreateNamedPipeW`` was denied (FR-010).
    """
    if config.kind == "pipe":
        return _launch_bridge_pipe(
            config, log_path=log_path, on_accept=on_accept, env=env,
        )

    # Pre-flight: is the port already occupied by a different process?
    if _port_is_occupied(config.host, config.port):
        raise PortInUseError(
            f"tcp port {config.host}:{config.port} already in use; "
            "pick a different --port or stop the process holding it"
        )

    bridge_js = _resolve_bridge_js()
    node_bin = _resolve_node_binary()

    cmd = [
        node_bin, str(bridge_js),
        "--transport", "tcp",
        "--host", config.host,
        "--port", str(config.port),
        "--data-dir", str(config.data_dir),
    ]

    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "bufsize": 1,
        "text": True,
        "env": {**os.environ, **(env or {})},
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
    except FileNotFoundError as e:
        raise BridgeSpawnError(f"failed to spawn node: {e}") from e

    log_file: TextIO | None = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")

    events: list[AcceptEvent] = []
    assert proc.stdout is not None and proc.stderr is not None
    out_t = _reader_thread(proc.stdout, sys.stdout, log_file, events, on_accept)
    err_t = _reader_thread(proc.stderr, sys.stderr, log_file, events, on_accept)

    handle = BridgeHandle(
        process=proc,
        config=config,
        log_path=log_path,
        _stdout_thread=out_t,
        _stderr_thread=err_t,
        _accept_events=events,
    )
    atexit.register(handle.terminate)

    # Poll for readiness.
    deadline = time.monotonic() + READINESS_TIMEOUT_SECS
    while True:
        rc = proc.poll()
        if rc is not None:
            # Exit code 5 from the bridge is our EADDRINUSE convention.
            if rc == 5:
                raise PortInUseError(
                    f"tcp port {config.host}:{config.port} already in use "
                    "(reported by bridge); pick a different --port"
                )
            raise BridgeSpawnError(
                f"bridge exited prematurely with code {rc} "
                f"before accepting probe connections"
            )
        status = _probe_tcp(config.host, config.port)
        if status == "ready":
            return handle
        if time.monotonic() >= deadline:
            handle.terminate()
            raise BridgeStartTimeout(
                f"bridge did not accept tcp {config.host}:{config.port} "
                f"within {READINESS_TIMEOUT_SECS:.0f}s; "
                "check stderr above for the underlying error"
            )
        time.sleep(READINESS_POLL_INTERVAL_SECS)


def _launch_bridge_pipe(
    config: TransportConfig,
    *,
    log_path: Path | None,
    on_accept: Callable[[AcceptEvent], None] | None,
    env: dict[str, str] | None,
) -> BridgeHandle:
    """Pipe-transport branch of :func:`launch_bridge` (T031 / T031b).

    Pre-flight: if the stable pipe already exists we exit 5 with the
    FR-026 message. Otherwise spawn Node, poll ``WaitNamedPipeW`` for
    readiness, and wire an in-process TCP relay that psycopg can dial.
    """
    import pywintypes
    import win32pipe

    pipe_name = config.resolved_pipe_name
    pipe_path = config.pipe_path

    if _pipe_exists(pipe_path):
        raise PortInUseError(
            f"named pipe {pipe_path} already in use; "
            f"rerun with --unique-pipe to pick a per-process name "
            f"(stem={pipe_name!r})"
        )

    bridge_js = _resolve_bridge_js()
    node_bin = _resolve_node_binary()

    cmd = [
        node_bin, str(bridge_js),
        "--transport", "pipe",
        "--pipe-name", pipe_name,
        "--data-dir", str(config.data_dir),
    ]

    popen_kwargs: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "bufsize": 1,
        "text": True,
        "env": {**os.environ, **(env or {})},
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # type: ignore[arg-type]
    except FileNotFoundError as e:
        raise BridgeSpawnError(f"failed to spawn node: {e}") from e

    log_file: TextIO | None = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")

    events: list[AcceptEvent] = []
    started = threading.Event()
    assert proc.stdout is not None and proc.stderr is not None
    out_t = _reader_thread(
        proc.stdout, sys.stdout, log_file, events, on_accept, started=started,
    )
    err_t = _reader_thread(proc.stderr, sys.stderr, log_file, events, on_accept)

    relay = PipeRelay(pipe_path)
    handle = BridgeHandle(
        process=proc,
        config=config,
        log_path=log_path,
        _stdout_thread=out_t,
        _stderr_thread=err_t,
        _accept_events=events,
        relay=relay,
    )
    atexit.register(handle.terminate)

    # Readiness: wait for the bridge's `[bridge] start` log line (Node has
    # bound the listening pipe instance by then). WaitNamedPipe is unsuited
    # for this because it returns FALSE when the pipe doesn't yet exist —
    # it only waits on BUSY, not ABSENT.
    deadline = time.monotonic() + READINESS_TIMEOUT_SECS
    while True:
        rc = proc.poll()
        if rc is not None:
            if rc == 5:
                raise PortInUseError(
                    f"named pipe {pipe_path} already in use "
                    "(reported by bridge); rerun with --unique-pipe"
                )
            if rc == 4:
                raise BridgePipeDenied(
                    f"transport 'pipe' unavailable: bridge reported "
                    f"CreateNamedPipeW denial for {pipe_path}; "
                    "rerun with --transport tcp to use the TCP listener instead"
                )
            raise BridgeSpawnError(
                f"bridge exited prematurely with code {rc} "
                "before accepting clients on the pipe"
            )
        if started.wait(timeout=READINESS_POLL_INTERVAL_SECS):
            break
        if time.monotonic() >= deadline:
            handle.terminate()
            raise BridgeStartTimeout(
                f"bridge did not present {pipe_path} "
                f"within {READINESS_TIMEOUT_SECS:.0f}s; "
                "check stderr above for the underlying error"
            )

    relay.start()
    return handle


@contextlib.contextmanager
def bridge_process(
    config: TransportConfig,
    **kwargs: object,
) -> Iterator[BridgeHandle]:
    """Context-manager wrapper around :func:`launch_bridge`."""
    handle = launch_bridge(config, **kwargs)  # type: ignore[arg-type]
    try:
        yield handle
    finally:
        handle.terminate()
