"""
App-level "second layer" scoping on top of RLS — see V2_ARCHITECTURE.md §3: RLS
stops cross-council leaks even when a view has a bug; this stops a CONSULTANT from
seeing another consultant's own-council rows, which RLS (scoped to council, not
consultant) doesn't and shouldn't try to enforce.

A CONSULTANT's "portfolio" for visibility purposes is every payer enumerated by one
of their own users (managers or agents) — matching the prototype's `portfolio_filter()`
pattern (TDD.md §4.2) ported to the ORM.

REVENUE_OFFICER gets the exact same scoping as CONSULTANT — a revenue officer is
assigned to one consultant (via the same AppUser.consultant FK) and is meant to see
that consultant's whole portfolio, just without any mutation rights. Read-only is
enforced at the permission_classes/get_permissions level on each view, not here.
"""
from apps.accounts.models import AppRole

_PORTFOLIO_SCOPED_LEVELS = (AppRole.CONSULTANT, AppRole.REVENUE_OFFICER)


def portfolio_filter(queryset, request, payer_path="payer"):
    user = request.user
    if user.access_level not in _PORTFOLIO_SCOPED_LEVELS:
        return queryset
    lookup = f"{payer_path}__enumerated_by__consultant_id" if payer_path else "enumerated_by__consultant_id"
    return queryset.filter(**{lookup: user.consultant_id})
