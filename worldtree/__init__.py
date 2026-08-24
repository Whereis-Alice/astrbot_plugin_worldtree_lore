"""Core domain package for WorldTree Lore.

The package deliberately has no AstrBot imports so its validation, import and
session behaviour can be tested without starting a bot instance.
"""

from .library import RevisionConflict, WorldTreeLibrary
from .models import ActivationContext, EntryValidationError, WorldTreeEntry
from .sessions import WorldTreeSessionStore

__all__ = [
    "ActivationContext",
    "EntryValidationError",
    "RevisionConflict",
    "WorldTreeEntry",
    "WorldTreeLibrary",
    "WorldTreeSessionStore",
]
