"""Product and tenant presentation names -- one place, nothing renamed.

    PRODUCT:  Banna Dispatch
    TENANT:   Luxor Locksmith

WHY A MODULE AND NOT A FIND-AND-REPLACE
The string "LUXOR" is load-bearing in this repository: LUXOR_* environment
variables, database rows, fixtures, test assertions, script names and
historical documents all use it, and business records must not be
rewritten for branding. So nothing is renamed. This module supplies the
names the USER INTERFACE shows, and only that.

The tenant is data, not a fork: pointing this at another locksmith is a
configuration change (BANNA_TENANT_NAME), not a second application.
"""
from __future__ import annotations

import os

# Defaults. Overridable per deployment without touching any LUXOR_* name.
PRODUCT_NAME = os.environ.get("BANNA_PRODUCT_NAME", "BANNA DISPATCH")
PRODUCT_SHORT_NAME = os.environ.get("BANNA_PRODUCT_SHORT_NAME", "BANNA")
TENANT_NAME = os.environ.get("BANNA_TENANT_NAME", "Luxor Locksmith")


def branding() -> dict:
    """The payload the console renders its header from."""
    return {
        "product_name": PRODUCT_NAME,
        "product_short_name": PRODUCT_SHORT_NAME,
        "tenant_name": TENANT_NAME,
    }
