"""Pytest fixtures for PGlite integration - Framework Agnostic Core."""

import tempfile
import uuid

from collections.abc import Generator
from pathlib import Path

import pytest

from pglite_pydb.config import PGliteConfig
from pglite_pydb.manager import PGliteManager


def _make_session_data_dir(
    tmp_path_factory: pytest.TempPathFactory, prefix: str
) -> Path:
    """Return a unique tmp_path-based data_dir for a session/module fixture."""
    return tmp_path_factory.mktemp(f"{prefix}-{uuid.uuid4().hex[:8]}", numbered=False)


@pytest.fixture(scope="session")
def pglite_manager(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[PGliteManager, None, None]:
    """Pytest fixture providing a PGlite manager for the test session.

    This is the core, framework-agnostic fixture. Framework-specific
    fixtures build on top of this.

    Yields:
        PGliteManager: Active PGlite manager instance
    """
    data_dir = _make_session_data_dir(tmp_path_factory, "pglite-session-data")
    # Create unique configuration to prevent socket conflicts
    config = PGliteConfig(data_dir=data_dir)

    # Create a unique socket directory for this test session
    # PGlite expects socket_path to be the full path including .s.PGSQL.5432
    socket_dir = (
        Path(tempfile.gettempdir()) / f"pglite-pydb-test-{uuid.uuid4().hex[:8]}"
    )
    socket_dir.mkdir(mode=0o700, exist_ok=True)  # Restrict to user only
    config.socket_path = str(socket_dir / ".s.PGSQL.5432")

    manager = PGliteManager(config)
    manager.start()
    manager.wait_for_ready()

    try:
        yield manager
    finally:
        manager.stop()


@pytest.fixture(scope="module")
def pglite_manager_isolated(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[PGliteManager, None, None]:
    """Pytest fixture providing an isolated PGlite manager per test module.

    Use this fixture when you need stronger isolation between test modules
    to prevent cross-test interference.

    Yields:
        PGliteManager: Active PGlite manager instance
    """
    data_dir = _make_session_data_dir(tmp_path_factory, "pglite-module-data")
    # Create unique configuration to prevent socket conflicts
    config = PGliteConfig(data_dir=data_dir)

    # Create a unique socket directory for this test module
    # PGlite expects socket_path to be the full path including .s.PGSQL.5432
    socket_dir = (
        Path(tempfile.gettempdir()) / f"pglite-pydb-module-{uuid.uuid4().hex[:8]}"
    )
    socket_dir.mkdir(mode=0o700, exist_ok=True)  # Restrict to user only
    config.socket_path = str(socket_dir / ".s.PGSQL.5432")

    manager = PGliteManager(config)
    manager.start()
    manager.wait_for_ready()

    try:
        yield manager
    finally:
        manager.stop()


# Additional configuration fixtures
@pytest.fixture(scope="session")
def pglite_config(tmp_path_factory: pytest.TempPathFactory) -> PGliteConfig:
    """Pytest fixture providing PGlite configuration.

    Override this fixture in your conftest.py to customize PGlite settings.

    Returns:
        PGliteConfig: Configuration for PGlite
    """
    data_dir = _make_session_data_dir(tmp_path_factory, "pglite-config-data")
    return PGliteConfig(data_dir=data_dir)


@pytest.fixture(scope="session")
def pglite_manager_custom(
    pglite_config: PGliteConfig,
) -> Generator[PGliteManager, None, None]:
    """Pytest fixture providing a PGlite manager with custom configuration.

    Args:
        pglite_config: Custom configuration

    Yields:
        PGliteManager: Active PGlite manager instance
    """
    # Ensure unique socket path even with custom config
    if not hasattr(pglite_config, "socket_path") or not pglite_config.socket_path:
        socket_dir = (
            Path(tempfile.gettempdir()) / f"pglite-pydb-custom-{uuid.uuid4().hex[:8]}"
        )
        socket_dir.mkdir(mode=0o700, exist_ok=True)  # Restrict to user only
        pglite_config.socket_path = str(socket_dir / ".s.PGSQL.5432")

    manager = PGliteManager(pglite_config)
    manager.start()
    manager.wait_for_ready()

    try:
        yield manager
    finally:
        manager.stop()
