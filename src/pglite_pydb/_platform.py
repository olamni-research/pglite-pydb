"""Centralised platform detection for pglite-pydb.

All ``sys.platform`` / ``os.name`` checks elsewhere in ``pglite_pydb``
MUST import from this module. Adding a new platform branch anywhere else
is a code-review red flag — put it here instead.

AF_UNIX exists on Windows 10+, but PGlite's Node server writes socket
paths that Windows psycopg cannot portably address. TCP loopback is the
Windows transport for this project. See
``specs/001-pglite-pydb-port/research.md`` R2.
"""

from __future__ import annotations

import sys


IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_MACOS: bool = sys.platform == "darwin"

SUPPORTS_UNIX_SOCKETS: bool = not IS_WINDOWS

__all__ = [
    "IS_LINUX",
    "IS_MACOS",
    "IS_WINDOWS",
    "SUPPORTS_UNIX_SOCKETS",
]
