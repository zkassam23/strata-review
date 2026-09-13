"""Tenant lookup. Single tenant read from config today; replace get_tenant with a table query
later and nothing else in the web layer changes."""
from __future__ import annotations

from typing import Any

from strata_review.settings import Settings


def get_tenant(settings: Settings, tenant_id: str = "default") -> dict[str, Any]:
    t = dict(settings.tenant)
    if t.get("id", "default") != tenant_id:
        raise KeyError(tenant_id)
    return t
