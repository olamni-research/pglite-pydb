"""Test coverage for import statements and module exports.

This file tests all the 0% coverage __init__.py files and extensions
to boost overall coverage with simple import tests.
"""

import pytest


def test_main_package_imports():
    """Test main package imports work correctly."""
    # Test all imports from pglite_pydb/__init__.py (lines 7-16)
    from pglite_pydb import AsyncpgClient
    from pglite_pydb import PGliteConfig
    from pglite_pydb import PGliteManager
    from pglite_pydb import PsycopgClient
    from pglite_pydb import get_client
    from pglite_pydb import get_default_client

    # Verify classes can be instantiated (basic smoke test)
    config = PGliteConfig()
    assert config.timeout == 30
    assert config.cleanup_on_exit is True

    manager = PGliteManager(config)
    assert manager.config == config

    # Test client getters work
    client = get_default_client()
    assert client is not None

    auto_client = get_client("auto")
    assert auto_client is not None


def test_django_package_imports():
    """Test Django package imports work correctly."""
    # Test all imports from pglite_pydb/django/__init__.py (lines 7-18)
    from pglite_pydb.django import configure_django_for_pglite
    from pglite_pydb.django import create_django_superuser
    from pglite_pydb.django import db
    from pglite_pydb.django import django_pglite_db
    from pglite_pydb.django import django_pglite_transactional_db
    from pglite_pydb.django import transactional_db

    # Verify these are callable/importable
    assert callable(configure_django_for_pglite)
    assert callable(create_django_superuser)
    # Note: fixture functions will be pytest fixtures, not directly callable


def test_sqlalchemy_package_imports():
    """Test SQLAlchemy package imports work correctly."""
    # Test all imports from pglite_pydb/sqlalchemy/__init__.py (lines 7-20)
    from pglite_pydb.sqlalchemy import SQLAlchemyAsyncPGliteManager
    from pglite_pydb.sqlalchemy import SQLAlchemyPGliteManager
    from pglite_pydb.sqlalchemy import create_all_tables
    from pglite_pydb.sqlalchemy import drop_all_tables
    from pglite_pydb.sqlalchemy import get_session_class
    from pglite_pydb.sqlalchemy import pglite_async_engine
    from pglite_pydb.sqlalchemy import pglite_async_session
    from pglite_pydb.sqlalchemy import pglite_engine
    from pglite_pydb.sqlalchemy import pglite_session
    from pglite_pydb.sqlalchemy import pglite_sqlalchemy_async_engine
    from pglite_pydb.sqlalchemy import pglite_sqlalchemy_engine
    from pglite_pydb.sqlalchemy import pglite_sqlalchemy_session

    # Verify manager classes can be imported
    assert SQLAlchemyAsyncPGliteManager is not None
    assert SQLAlchemyPGliteManager is not None

    # Verify utilities are callable
    assert callable(create_all_tables)
    assert callable(drop_all_tables)
    assert callable(get_session_class)


def test_extensions_registry():
    """Test extensions registry is accessible."""
    # Test pglite_pydb/extensions.py (line 7)
    from pglite_pydb.extensions import SUPPORTED_EXTENSIONS

    # Verify registry structure
    assert isinstance(SUPPORTED_EXTENSIONS, dict)
    assert "pgvector" in SUPPORTED_EXTENSIONS

    # Verify pgvector extension details
    pgvector = SUPPORTED_EXTENSIONS["pgvector"]
    assert pgvector["module"] == "@electric-sql/pglite/vector"
    assert pgvector["name"] == "vector"


def test_all_exports_available():
    """Test that all __all__ exports are available."""
    # Test main package __all__
    import pglite_pydb

    for name in pglite_pydb.__all__:
        assert hasattr(pglite_pydb, name), f"Missing export: {name}"

    # Test Django package __all__
    import pglite_pydb.django

    for name in pglite_pydb.django.__all__:
        assert hasattr(pglite_pydb.django, name), f"Missing Django export: {name}"

    # Test SQLAlchemy package __all__
    import pglite_pydb.sqlalchemy

    for name in pglite_pydb.sqlalchemy.__all__:
        assert hasattr(pglite_pydb.sqlalchemy, name), f"Missing SQLAlchemy export: {name}"


def test_package_metadata():
    """Test package metadata is accessible."""
    import pglite_pydb

    # Test __version__ is accessible (part of lines 7-16)
    assert hasattr(pglite_pydb, "__version__")
    assert isinstance(pglite_pydb.__version__, str)
    assert pglite_pydb.__version__ != ""
