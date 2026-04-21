"""pglite-pydb: Python testing library for PGlite integration.

Provides seamless integration between PGlite (in-memory PostgreSQL)
and Python test suites with support for SQLAlchemy, SQLModel, and Django.
"""

import importlib.metadata


__version__ = importlib.metadata.version(__name__)

# Core exports (always available)
# Database client exports (choose your preferred client)
from pglite_pydb.clients import AsyncpgClient
from pglite_pydb.clients import PsycopgClient
from pglite_pydb.clients import get_client
from pglite_pydb.clients import get_default_client
from pglite_pydb.config import PGliteConfig
from pglite_pydb.config import SidecarConfig
from pglite_pydb.errors import BackupLocationNotConfiguredError
from pglite_pydb.errors import BackupLocationUnavailableError
from pglite_pydb.errors import BackupSelectorMissingError
from pglite_pydb.errors import ConfirmationDeclinedError
from pglite_pydb.errors import ConfirmationRequiredError
from pglite_pydb.errors import ContainerKindMismatchError
from pglite_pydb.errors import CorruptContainerError
from pglite_pydb.errors import InstanceInUseError
from pglite_pydb.errors import InvalidDataDirError
from pglite_pydb.errors import MissingDataDirError
from pglite_pydb.errors import NoBackupsFoundError
from pglite_pydb.errors import PGlitePydbError
from pglite_pydb.errors import RestoreConflictError
from pglite_pydb.errors import SchemaNotFoundError
from pglite_pydb.manager import PGliteManager


# Core public API - framework agnostic
__all__ = [
    "AsyncpgClient",
    "BackupLocationNotConfiguredError",
    "BackupLocationUnavailableError",
    "BackupSelectorMissingError",
    "ConfirmationDeclinedError",
    "ConfirmationRequiredError",
    "ContainerKindMismatchError",
    "CorruptContainerError",
    "InstanceInUseError",
    "InvalidDataDirError",
    "MissingDataDirError",
    "NoBackupsFoundError",
    "PGliteConfig",
    "PGliteManager",
    "PGlitePydbError",
    "PsycopgClient",
    "RestoreConflictError",
    "SchemaNotFoundError",
    "SidecarConfig",
    # Database clients
    "get_client",
    "get_default_client",
]

# Framework integrations are imported separately:
# from pglite_pydb.sqlalchemy import pglite_session, pglite_engine
# from pglite_pydb.django import db, transactional_db
# Or use the pytest plugin which auto-discovers fixtures
