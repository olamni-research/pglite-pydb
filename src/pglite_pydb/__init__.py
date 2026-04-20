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
from pglite_pydb.manager import PGliteManager


# Core public API - framework agnostic
__all__ = [
    "AsyncpgClient",
    "PGliteConfig",
    "PGliteManager",
    "PsycopgClient",
    # Database clients
    "get_client",
    "get_default_client",
]

# Framework integrations are imported separately:
# from pglite_pydb.sqlalchemy import pglite_session, pglite_engine
# from pglite_pydb.django import db, transactional_db
# Or use the pytest plugin which auto-discovers fixtures
