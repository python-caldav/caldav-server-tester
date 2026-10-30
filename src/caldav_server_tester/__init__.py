## Before anything else looks up a feature: see future_features.
from . import future_features

future_features.register()

from .caldav_server_tester import (  # noqa: E402
    check_server_compatibility as check_server_compatibility,
)
from .checker import ServerQuirkChecker as ServerQuirkChecker  # noqa: E402

__all__ = ["check_server_compatibility", "ServerQuirkChecker"]
