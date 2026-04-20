"""Django integration for pglite-pydb.

This module provides Django-specific fixtures and utilities for pglite-pydb.
"""

# Import fixtures and utilities
from pglite_pydb.django.fixtures import db
from pglite_pydb.django.fixtures import django_pglite_db
from pglite_pydb.django.fixtures import django_pglite_transactional_db
from pglite_pydb.django.fixtures import transactional_db
from pglite_pydb.django.utils import configure_django_for_pglite
from pglite_pydb.django.utils import create_django_superuser


__all__ = [
    "configure_django_for_pglite",
    "create_django_superuser",
    "db",
    "django_pglite_db",
    "django_pglite_transactional_db",
    "transactional_db",
]
