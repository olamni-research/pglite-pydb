"""
Django Testing Configuration for pglite-pydb
==========================================

Provides fixtures and configuration for both Django integration patterns:
• Pattern 1: Lightweight/Socket approach (standard PostgreSQL backend)
• Pattern 2: Full Integration approach (custom pglite-pydb backend)

Shows proper abstraction for different testing approaches.
"""

import django
import pytest

from django.conf import settings

from pglite_pydb import PGliteConfig
from pglite_pydb import PGliteManager


@pytest.fixture(scope="function")
def pglite_manager():
    """
    🎯 Base PGlite manager fixture

    Provides fresh PGlite instance for each test.
    Used by both Django pattern approaches.
    """
    manager = PGliteManager(PGliteConfig())
    manager.start()

    try:
        yield manager
    finally:
        manager.stop()


@pytest.fixture(autouse=True)
def setup_django_mail():
    """
    🎯 Ensure Django mail outbox for pytest-django

    This autouse fixture ensures mail.outbox is always available
    for pytest-django compatibility.
    """
    try:
        from django.core import mail

        if not hasattr(mail, "outbox"):
            mail.outbox = []
        else:
            mail.outbox.clear()
    except ImportError:
        pass  # Django not configured yet


@pytest.fixture(scope="function")
def configured_django(pglite_manager):
    """
    🔹 Pattern 1: Lightweight/Socket Django Configuration

    Provides Django setup with standard PostgreSQL backend via socket.
    This is the main fixture for the lightweight pattern.

    Features:
    • django.db.backends.postgresql (standard backend)
    • Direct socket connection to PGlite
    • Minimal setup and dependencies
    • Fast and simple
    """
    # Get connection details from PGlite
    conn_str = pglite_manager.config.get_connection_string()
    socket_dir = conn_str.split("host=")[1].split("&")[0].split("#")[0]

    # Configure Django if not already configured
    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.postgresql",  # Standard backend
                    "NAME": "postgres",
                    "USER": "postgres",
                    "PASSWORD": "postgres",
                    "HOST": socket_dir,
                    "PORT": "",
                    "OPTIONS": {"connect_timeout": 10},
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
            ],
            USE_TZ=False,  # Avoid timezone conflicts
            SECRET_KEY="django-pglite-lightweight-testing",
            # pytest-django compatibility
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        )
        django.setup()

        # Initialize mail outbox for pytest-django
        from django.core import mail

        if not hasattr(mail, "outbox"):
            mail.outbox = []
    else:
        # Update existing configuration with new PGlite connection
        settings.DATABASES["default"]["HOST"] = socket_dir

        # Reset connections to use new database
        from django.db import connections

        connections.close_all()

    yield


@pytest.fixture(scope="function")
def django_pglite_db(pglite_manager):
    """
    🔸 Pattern 2: Full Integration Django Configuration

    Provides Django setup with custom pglite-pydb backend.
    This is the main fixture for the full integration pattern.

    Features:
    • pglite_pydb.django.backend (custom backend)
    • Full pglite-pydb integration features
    • Advanced backend capabilities
    • Enhanced optimization
    """
    # Get connection details from PGlite - just like the socket pattern
    conn_str = pglite_manager.config.get_connection_string()
    socket_dir = conn_str.split("host=")[1].split("&")[0].split("#")[0]

    # Configure Django if not already configured
    if not settings.configured:
        settings.configure(
            DATABASES={
                "default": {
                    "ENGINE": "pglite_pydb.django.backend",  # Custom pglite-pydb backend
                    "NAME": "postgres",
                    "USER": "postgres",
                    "PASSWORD": "postgres",
                    "HOST": socket_dir,  # Use PGlite socket, not localhost!
                    "PORT": "",
                    "OPTIONS": {"connect_timeout": 10},
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
            ],
            USE_TZ=False,  # Avoid timezone conflicts
            SECRET_KEY="django-pglite-backend-testing",
            # pytest-django compatibility
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        )
        django.setup()

        # Initialize mail outbox for pytest-django
        from django.core import mail

        if not hasattr(mail, "outbox"):
            mail.outbox = []
    else:
        # Update existing configuration with new PGlite connection
        settings.DATABASES["default"]["HOST"] = socket_dir

        # Reset connections to ensure clean state
        from django.db import connections

        connections.close_all()

    yield


@pytest.fixture
def django_user_model(configured_django):
    """
    🎯 Django User model fixture (Lightweight Pattern)

    Example of providing Django model fixtures for socket pattern.
    Shows how to abstract common Django testing patterns.
    """
    from django.contrib.auth.models import User

    return User


@pytest.fixture
def django_backend_user_model(django_pglite_db):
    """
    🎯 Django User model fixture (Full Integration Pattern)

    Example of providing Django model fixtures for backend pattern.
    Shows how to abstract common Django testing patterns.
    """
    from django.contrib.auth.models import User

    return User


@pytest.fixture
def pattern_comparison_data():
    """
    🔄 Comparison data for pattern testing

    Provides standardized data for comparing both patterns.
    """
    return {
        "test_products": [
            {"name": "Widget A", "price": 19.99, "active": True},
            {"name": "Widget B", "price": 29.99, "active": False},
            {"name": "Widget C", "price": 39.99, "active": True},
        ],
        "test_categories": [
            {"name": "Electronics", "slug": "electronics"},
            {"name": "Software", "slug": "software"},
            {"name": "Hardware", "slug": "hardware"},
        ],
        "test_metadata": {
            "version": "1.0",
            "features": ["json", "testing", "patterns"],
            "config": {"debug": True, "performance": "high"},
        },
    }
