"""Configuration for PGlite testing."""

import json
import logging
import os
import tempfile
import uuid

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Literal

from pglite_pydb._datadir import SIDECAR_DIRNAME
from pglite_pydb._datadir import is_rejectable
from pglite_pydb._platform import IS_WINDOWS
from pglite_pydb.errors import InvalidDataDirError
from pglite_pydb.errors import MissingDataDirError
from pglite_pydb.extensions import SUPPORTED_EXTENSIONS


_logger = logging.getLogger(__name__)

_SIDECAR_FILENAME = "config.json"
_SIDECAR_SCHEMA_VERSION = 1


@dataclass
class SidecarConfig:
    """Per-instance persisted state under ``<data-dir>/.pglite-pydb/config.json``.

    See ``specs/003-pglite-path-backup-restore/data-model.md`` §3.
    """

    backup_location: str | None = None
    schema_version: int = _SIDECAR_SCHEMA_VERSION

    @classmethod
    def load(cls, data_dir: Path) -> "SidecarConfig":
        """Load the sidecar for ``data_dir``.

        Missing sidecar → default instance (no file is created).
        Unknown ``schema_version`` → ValueError with upgrade hint.
        """
        path = Path(data_dir) / SIDECAR_DIRNAME / _SIDECAR_FILENAME
        if not path.exists():
            return cls()
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        sv = raw.get("schema_version")
        if sv != _SIDECAR_SCHEMA_VERSION:
            raise ValueError(
                f"Sidecar config at {path!s} has unsupported schema_version={sv!r}. "
                "Upgrade pglite-pydb."
            )
        return cls(
            backup_location=raw.get("backup_location"),
            schema_version=sv,
        )

    def save(self, data_dir: Path) -> Path:
        """Persist this sidecar under ``data_dir``; returns the written path."""
        sidecar_dir = Path(data_dir) / SIDECAR_DIRNAME
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        path = sidecar_dir / _SIDECAR_FILENAME
        payload = {
            "schema_version": self.schema_version,
            "backup_location": self.backup_location,
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        return path


def _get_secure_socket_path() -> str:
    """Generate a secure socket path in user's temp directory."""
    # Use both PID and UUID to ensure uniqueness
    unique_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    temp_dir = Path(tempfile.gettempdir()) / f"pglite-pydb-{unique_id}"
    temp_dir.mkdir(mode=0o700, exist_ok=True)  # Restrict to user only
    # Use PostgreSQL's standard socket naming convention
    return str(temp_dir / ".s.PGSQL.5432")


@dataclass
class PGliteConfig:
    """Configuration for PGlite test database.

    Args:
        timeout: Timeout in seconds for PGlite startup (default: 30)
        cleanup_on_exit: Whether to cleanup socket/process on exit (default: True)
        log_level: Logging level for PGlite operations (default: "INFO")
        socket_path: Custom socket path (default: secure temp directory)
        work_dir: Working directory for PGlite files (default: None, uses temp)
        node_modules_check: Whether to verify node_modules exists (default: True)
        auto_install_deps: Whether to auto-install npm dependencies (default: True)
        extensions: List of PGlite extensions to enable (e.g., ["pgvector"])
        node_options: Custom NODE_OPTIONS for the Node.js process
        use_tcp: Use TCP socket instead of Unix domain socket.
            Default behaviour is platform-conditional:
            - Linux / macOS: default is False (Unix domain socket)
            - Windows:       default is True  (TCP, auto-promoted)
            Pass True/False explicitly to override the default. Passing
            False on Windows raises RuntimeError (Unix sockets are not
            a supported transport on Windows — see research.md R2).
        tcp_host: TCP host to bind to when use_tcp is True (default: "127.0.0.1")
        tcp_port: TCP port to bind to when use_tcp is True.
            Default behaviour is platform-conditional:
            - Linux / macOS: 5432 (explicit TCP requires explicit port
              in most setups; 5432 is the PostgreSQL default)
            - Windows:       0 when auto-promoted (OS-assigned ephemeral
              port; see research.md R4). Pass an integer to pin.
    """

    # data_dir becomes mandatory in feature 003 (FR-001). We use a sentinel
    # default of None so the dataclass can keep its existing
    # all-defaulted-fields layout; __post_init__ enforces presence.
    data_dir: Path | None = None
    timeout: int = 30
    cleanup_on_exit: bool = True
    log_level: str = "INFO"
    socket_path: str = field(default_factory=_get_secure_socket_path)
    work_dir: Path | None = None
    node_modules_check: bool = True
    auto_install_deps: bool = True
    extensions: list[str] | None = None
    node_options: str | None = None
    # use_tcp / tcp_port defaults are None (sentinel) so __post_init__ can
    # distinguish "user did not set" from "user set to False/5432 explicitly".
    # After __post_init__ runs both attributes always hold concrete values
    # (bool and int), matching the public contract in contracts/public-api.md.
    use_tcp: bool | None = None
    tcp_host: str = "127.0.0.1"
    tcp_port: int | None = None

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # data_dir: mandatory (FR-001); resolve symlinks (FR-003); reject
        # paths that already point at non-PGlite content (FR-005).
        if self.data_dir is None:
            raise MissingDataDirError()
        resolved = Path(self.data_dir).resolve(strict=False)
        if resolved.exists() and not resolved.is_dir():
            raise InvalidDataDirError(
                resolved, "path exists but is not a directory"
            )
        if is_rejectable(resolved):
            raise InvalidDataDirError(
                resolved,
                "directory is non-empty and does not contain a PGlite data layout",
            )
        self.data_dir = resolved

        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

        if self.log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            raise ValueError(f"Invalid log_level: {self.log_level}")

        if self.extensions:
            for ext in self.extensions:
                if ext not in SUPPORTED_EXTENSIONS:
                    raise ValueError(
                        f"Unsupported extension: '{ext}'. "
                        f"Available extensions: {list(SUPPORTED_EXTENSIONS.keys())}"
                    )

        if self.work_dir is not None:
            self.work_dir = Path(self.work_dir).resolve()

        # Resolve platform-conditional defaults for use_tcp / tcp_port.
        # See spec FR-003/FR-004/FR-005, research.md R2/R4.
        if self.use_tcp is None:
            # User did not specify use_tcp — apply platform default.
            if IS_WINDOWS:
                self.use_tcp = True
                _logger.info(
                    "Windows host detected: auto-selecting TCP transport "
                    "(Unix domain sockets are not a supported pglite-pydb "
                    "transport on Windows). See research.md R2."
                )
            else:
                self.use_tcp = False
        elif self.use_tcp is False and IS_WINDOWS:
            raise RuntimeError(
                "pglite-pydb does not support Unix domain sockets on Windows "
                "(use_tcp=False is incompatible with sys.platform='win32'). "
                "Remove the explicit use_tcp=False to accept the Windows TCP "
                "default, or pass use_tcp=True explicitly. "
                "See specs/001-pglite-pydb-port/research.md R2 for rationale."
            )

        if self.tcp_port is None:
            # Port default: 0 (OS-assigned) on Windows when use_tcp was
            # auto-promoted; 5432 everywhere else (PostgreSQL default).
            self.tcp_port = 0 if IS_WINDOWS else 5432

        # Validate TCP configuration (only when TCP is the resolved transport)
        if self.use_tcp:
            # Port 0 means "let the OS choose an ephemeral port at bind time".
            if self.tcp_port != 0 and not (1 <= self.tcp_port <= 65535):
                raise ValueError(f"Invalid TCP port: {self.tcp_port}")
            if not self.tcp_host:
                raise ValueError("TCP host cannot be empty")

    @property
    def log_level_int(self) -> int:
        """Get logging level as integer."""
        level_value = getattr(logging, self.log_level)
        return int(level_value)

    def get_connection_string(
        self, driver: Literal["psycopg", "psycopg2"] = "psycopg"
    ) -> str:
        """Get PostgreSQL connection string for SQLAlchemy usage.

        Note: when ``tcp_port == 0`` (OS-assigned ephemeral port), callers
        MUST construct the connection string via the running
        ``PGliteManager`` (its ``resolved_port`` attribute holds the real
        port after ``start()`` returns). This method will emit ``:0`` in
        that case, which is not a valid connection string.
        """
        if self.use_tcp:
            # TCP connection string
            return f"postgresql+{driver}://postgres:postgres@{self.tcp_host}:{self.tcp_port}/postgres?sslmode=disable"

        # For SQLAlchemy with Unix domain sockets, we need to specify the directory
        # and use the standard PostgreSQL socket naming convention
        socket_dir = str(Path(self.socket_path).parent)

        # Use the socket directory as host - psycopg will look for .s.PGSQL.5432
        connection_string = (
            f"postgresql+{driver}://postgres:postgres@/postgres?host={socket_dir}"
        )

        return connection_string

    def get_psycopg_uri(self) -> str:
        """Get PostgreSQL URI for direct psycopg usage."""
        if self.use_tcp:
            # TCP URI
            return f"postgresql://postgres:postgres@{self.tcp_host}:{self.tcp_port}/postgres?sslmode=disable"

        socket_dir = str(Path(self.socket_path).parent)
        # Use standard PostgreSQL URI format for psycopg
        return f"postgresql://postgres:postgres@/postgres?host={socket_dir}"

    def get_dsn(self) -> str:
        """Get PostgreSQL DSN connection string for direct psycopg usage."""
        if self.use_tcp:
            # TCP DSN
            return f"host={self.tcp_host} port={self.tcp_port} dbname=postgres user=postgres password=postgres sslmode=disable"

        socket_dir = str(Path(self.socket_path).parent)
        # Use key-value format for psycopg DSN, including password
        return f"host={socket_dir} dbname=postgres user=postgres password=postgres"

    def get_asyncpg_uri(self) -> str:
        """Get PostgreSQL URI for asyncpg usage.

        Returns:
            PostgreSQL URI string compatible with asyncpg.connect()
        """
        if self.use_tcp:
            # TCP URI (asyncpg doesn't support sslmode parameter)
            return f"postgresql://postgres:postgres@{self.tcp_host}:{self.tcp_port}/postgres"

        # Unix socket URI
        socket_dir = str(Path(self.socket_path).parent)
        return f"postgresql://postgres:postgres@/postgres?host={socket_dir}"
