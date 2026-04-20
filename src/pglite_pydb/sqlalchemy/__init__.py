"""SQLAlchemy integration for pglite-pydb.

This module provides SQLAlchemy-specific fixtures and utilities for pglite-pydb.
"""

# Import fixtures and utilities
from pglite_pydb.sqlalchemy.fixtures import pglite_async_engine
from pglite_pydb.sqlalchemy.fixtures import pglite_async_session
from pglite_pydb.sqlalchemy.fixtures import pglite_async_sqlalchemy_manager
from pglite_pydb.sqlalchemy.fixtures import pglite_engine
from pglite_pydb.sqlalchemy.fixtures import pglite_session
from pglite_pydb.sqlalchemy.fixtures import pglite_sqlalchemy_async_engine
from pglite_pydb.sqlalchemy.fixtures import pglite_sqlalchemy_engine
from pglite_pydb.sqlalchemy.fixtures import pglite_sqlalchemy_session
from pglite_pydb.sqlalchemy.manager import SQLAlchemyPGliteManager
from pglite_pydb.sqlalchemy.manager_async import SQLAlchemyAsyncPGliteManager
from pglite_pydb.sqlalchemy.utils import create_all_tables
from pglite_pydb.sqlalchemy.utils import drop_all_tables
from pglite_pydb.sqlalchemy.utils import get_session_class


__all__ = [
    # Manager
    "SQLAlchemyAsyncPGliteManager",
    "SQLAlchemyPGliteManager",
    # Utilities
    "create_all_tables",
    "drop_all_tables",
    "get_session_class",
    # Fixtures
    "pglite_async_engine",
    "pglite_async_session",
    "pglite_async_sqlalchemy_manager",
    "pglite_engine",
    "pglite_session",
    "pglite_sqlalchemy_async_engine",
    "pglite_sqlalchemy_engine",
    "pglite_sqlalchemy_session",
]
