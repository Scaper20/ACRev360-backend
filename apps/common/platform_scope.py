"""
Cross-council read access for ACDSL/platform-tier roles (council=null on the
AppUser) — see docs/RBAC_EXPANSION_DESIGN.md. This deliberately does NOT add
an RLS bypass policy anywhere: FORCE ROW LEVEL SECURITY stays meaningful for
every table, always. Instead this loops over exactly the councils a given
user is entitled to see, evaluating one query per council inside that
council's own RLS context (apps.tenancy.context.council_context) — the same
proven pattern apps.tenancy.context.find_across_active_councils already uses
for anonymous receipt lookup, and migration_helpers.for_each_council uses for
data migrations. A wide, unscoped query is never made on a platform user's
behalf; N correctly-scoped ones are, and the results are concatenated.
"""
from itertools import chain

from django.utils import timezone

from apps.accounts.models import AppRole
from apps.tenancy.context import council_context

#: Access levels that read across every active council, not just
#: CouncilGrant-listed ones — the matrix's "global (read)"/"global"
#: platform roles.
_ALL_COUNCILS_LEVELS = (
    AppRole.SUPER_ADMIN,
    AppRole.PLATFORM_ADMIN,
    AppRole.BD_VIEW,
    AppRole.COMPLIANCE_VIEW,
    AppRole.ANALYTICS_VIEW,
    AppRole.FINANCE_ADMIN,
)

#: Access levels restricted to exactly the councils named in their own
#: (non-expired) CouncilGrant rows — the matrix's "time-boxed" tier.
_GRANT_SCOPED_LEVELS = (AppRole.EXTERNAL_AUDITOR,)


def is_platform_wide(user) -> bool:
    """True for any access level that reads across more than its own single
    council — i.e. every level handled by platform_wide_queryset below."""
    return user.access_level in _ALL_COUNCILS_LEVELS or user.access_level in _GRANT_SCOPED_LEVELS


def granted_council_ids(user) -> list[int]:
    """Non-expired CouncilGrant council ids for this user. Expired grants
    are excluded here, not deleted — the grant's own history (who could see
    what, and until when) is worth keeping, same rationale as
    PaymentAllocation/PayerDelegation rows outliving their active period."""
    now = timezone.now()
    return list(
        user.council_grants.filter(expires_at__isnull=True).values_list("council_id", flat=True)
    ) + list(
        user.council_grants.filter(expires_at__gt=now).values_list("council_id", flat=True)
    )


def accessible_council_ids(user) -> list[int]:
    """Every council id this platform-tier user may read. Callers should
    only reach here after confirming is_platform_wide(user) — a non-platform
    user just gets [user.council_id] back, which is what council_id=user.
    council_id filtering already does, so there's rarely a reason to call
    this for a council-scoped user directly."""
    from apps.tenancy.models import Council

    if user.access_level in _GRANT_SCOPED_LEVELS:
        return granted_council_ids(user)
    if user.access_level in _ALL_COUNCILS_LEVELS:
        return list(Council.objects.filter(is_active=True).order_by("id").values_list("id", flat=True))
    return [user.council_id] if user.council_id else []


def platform_wide_queryset(query_fn, user):
    """Run `query_fn(council_id)` once per council accessible to `user`
    (see accessible_council_ids), each inside that council's own RLS
    context, and concatenate the results. `query_fn` should return an
    iterable (a queryset is fine — it's evaluated inside the `with` block
    before the context is torn down, so wrap in list()/pass a sliced
    queryset if you need it materialized per call)."""
    results = []
    for council_id in accessible_council_ids(user):
        with council_context(council_id):
            results.append(list(query_fn(council_id)))
    return list(chain.from_iterable(results))
