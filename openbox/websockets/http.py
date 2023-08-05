from __future__ import annotations

import sys

from .imports import lazy_import


# For backwards compatibility:


lazy_import(
    globals(),
    # Headers and MultipleValuesError used to be defined in this module.
    aliases={
        "Headers": ".datastructures",
        "MultipleValuesError": ".datastructures",
    },
    deprecated_aliases={
        "read_request": ".legacy.http",
        "read_response": ".legacy.http",
    },
)


__all__ = ["USER_AGENT"]


PYTHON_VERSION = "{}.{}".format(*sys.version_info)
USER_AGENT = f"Python/{PYTHON_VERSION} websockets/nAn"
