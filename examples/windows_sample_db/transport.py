"""Transport configuration for the Windows sample-database example.

Pure-stdlib module; deliberately has no ``psycopg``/``pywin32`` imports at
module scope so it can be constructed even on non-Windows platforms for
testing / introspection. The live connection path (Slice B/C/D) imports
them locally inside the functions that actually need them.
"""

from __future__ import annotations

import os
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
        return unique_pipe_name(self.pipe_name) if self.unique_pipe else self.pipe_name

    @property
    def pipe_path(self) -> str:
        r"""Full Windows named-pipe path: ``\\.\pipe\<name>``."""
        return rf"\\.\pipe\{self.resolved_pipe_name}"

    def to_dsn(self) -> str:
        """psycopg 3 connection string for the TCP transport.

        For the named-pipe transport, psycopg 3 on Windows cannot consume
        a path as ``host``; the pipe path is wired via a pre-connected
        stream in Slice C (T028). This method intentionally errors in
        that case so callers use the dedicated pipe code path.
        """
        if self.kind != "tcp":
            raise ValueError(
                "to_dsn() is only valid for tcp transport; "
                "pipe transport goes through the pywin32 adapter"
            )
        return (
            f"host={self.host} port={self.port} "
            f"user={DEFAULT_ROLE} dbname={DEFAULT_DBNAME}"
        )
