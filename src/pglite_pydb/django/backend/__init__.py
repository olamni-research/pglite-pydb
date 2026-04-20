"""Django backend package for pglite-pydb integration."""

from pglite_pydb.django.backend.base import DatabaseWrapper
from pglite_pydb.django.backend.base import PGliteDatabaseCreation
from pglite_pydb.django.backend.base import PGliteDatabaseWrapper
from pglite_pydb.django.backend.base import get_pglite_manager


# Expose both names for compatibility
__all__ = [
    "DatabaseWrapper",
    "PGliteDatabaseCreation",
    "PGliteDatabaseWrapper",
    "get_pglite_manager",
]
