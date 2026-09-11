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

AGENT is scoped narrower still: not their consultant's whole portfolio, just the
payers they personally registered (Payer.enumerated_by is set to the acting user
at registration — see apps.registry.services.create_payer) — UNLESS a payer has
been explicitly handed to a *different* agent via Payer.assigned_agent
(FieldAgentViewSet.assign_payer), in which case it comes out of the original
registering agent's view entirely and only the assigned agent (and the
consultant manager, via the branch below) sees it. Checked before the
consultant-level branch since an agent's access_level is never CONSULTANT/
REVENUE_OFFICER, but keeping it a distinct first branch (rather than folding into
_PORTFOLIO_SCOPED_LEVELS with special-cased lookup) is what makes it obvious at a
glance that agents key off enumerated_by_id, not enumerated_by__consultant_id.
"""
from django.db.models import Q

from apps.accounts.models import AppRole

_PORTFOLIO_SCOPED_LEVELS = (AppRole.CONSULTANT, AppRole.REVENUE_OFFICER)


def portfolio_filter(queryset, request, payer_path="payer"):
    user = request.user
    if user.access_level == AppRole.AGENT:
        prefix = f"{payer_path}__" if payer_path else ""
        return queryset.filter(
            Q(**{f"{prefix}assigned_agent_id": user.id})
            | Q(**{f"{prefix}assigned_agent__isnull": True, f"{prefix}enumerated_by_id": user.id})
        )
    if user.access_level not in _PORTFOLIO_SCOPED_LEVELS:
        return queryset
    lookup = f"{payer_path}__enumerated_by__consultant_id" if payer_path else "enumerated_by__consultant_id"
    return queryset.filter(**{lookup: user.consultant_id})
