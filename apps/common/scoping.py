"""
App-level "second layer" scoping on top of RLS — see V2_ARCHITECTURE.md §3: RLS
stops cross-council leaks even when a view has a bug; this stops a CONSULTANT from
seeing another consultant's own-council rows, which RLS (scoped to council, not
consultant) doesn't and shouldn't try to enforce.

A CONSULTANT's "portfolio" for visibility purposes is every payer enumerated by one
of their own users (managers or agents) — matching the prototype's `portfolio_filter()`
pattern (TDD.md §4.2) ported to the ORM.
"""
from apps.accounts.models import AppRole


def portfolio_filter(queryset, request, payer_path="payer"):
    user = request.user
    if user.access_level != AppRole.CONSULTANT:
        return queryset
    lookup = f"{payer_path}__enumerated_by__consultant_id" if payer_path else "enumerated_by__consultant_id"
    return queryset.filter(**{lookup: user.consultant_id})
