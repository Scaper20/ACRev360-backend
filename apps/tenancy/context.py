"""
Helpers for setting the Postgres session-local tenant context that RLS policies
check (`current_setting('app.council_id', true)`). See V2_ARCHITECTURE.md §3.

Two ways this gets set:
  1. Per-request, for authenticated calls — apps.tenancy.middleware.CouncilContextMiddleware
     sets it once for the whole request from the JWT's `council_id` claim.
  2. Explicitly, for the handful of public/anonymous endpoints (bill lookup, receipt
     verification) that resolve their own target council from the request itself
     (a bill_ref's prefix, or by trying each active council) rather than from a user.

**Writing a data migration (RunPython)?** Neither of the above applies — a migration
gets no ambient context at all, and runs as the same non-superuser role RLS applies
to. A bare queryset against any RLS-protected table (most CouncilScopedModel tables —
grep `ENABLE ROW LEVEL SECURITY` for the current list) silently matches zero rows, and
reports success. This shipped for real on 2026-09-10 and destroyed production data —
see docs/CHANGELOG.md. Use `apps.tenancy.migration_helpers.for_each_council` instead of
a bare queryset in any RunPython that touches one of those tables.
"""
from contextlib import contextmanager

from django.core.cache import cache
from django.db import connection, transaction


def set_council_context(council_id: int | None) -> None:
    """Must be called inside an open transaction — SET LOCAL is transaction-scoped."""
    with connection.cursor() as cursor:
        if council_id is None:
            cursor.execute("SET LOCAL app.council_id = '';")
        else:
            cursor.execute("SET LOCAL app.council_id = %s;", [str(council_id)])


@contextmanager
def council_context(council_id: int | None):
    """Open a transaction scoped to a single council's RLS context."""
    with transaction.atomic():
        set_council_context(council_id)
        yield


#: bill_ref prefix -> council id, cached because every public lookup and every
#: channel webhook resolves its council from the reference itself — before this,
#: each of those requests opened one transaction and ran two queries per active
#: council just to answer "whose bill is KAC/...?". The answer changes only when
#: a council or its config is edited, which drops the entry (apps/tenancy/signals.py).
BILL_REF_PREFIX_CACHE_KEY = "tenancy:bill-ref-prefix-map"
_BILL_REF_PREFIX_TTL = 300


def _bill_ref_prefix_map() -> dict:
    mapping = cache.get(BILL_REF_PREFIX_CACHE_KEY)
    if mapping is None:
        from apps.tenancy.models import Council

        mapping = {}
        for council in Council.objects.filter(is_active=True).order_by("id"):
            with council_context(council.id):
                config = getattr(council, "config", None)
            if config and config.bill_ref_prefix:
                mapping.setdefault(config.bill_ref_prefix, council.id)
        cache.set(BILL_REF_PREFIX_CACHE_KEY, mapping, _BILL_REF_PREFIX_TTL)
    return mapping


def resolve_council_from_bill_ref(bill_ref: str):
    """A bill_ref (`KAC/2026/000123`) embeds its council's configured prefix — so
    public/pre-auth callers (bill lookup, channel webhooks) can resolve the target
    council from the reference itself, no bypass needed. See apps/billing and
    apps/channels public views."""
    if not isinstance(bill_ref, str):
        return None
    prefix = bill_ref.split("/")[0] if bill_ref else None
    if not prefix:
        return None
    council_id = _bill_ref_prefix_map().get(prefix)
    if council_id is None:
        return None

    from apps.tenancy.models import Council

    return Council.objects.filter(pk=council_id, is_active=True).first()


def find_across_active_councils(query_fn):
    """
    Run `query_fn(council)` once per active council, in its own RLS context, until one
    returns a truthy result. Used by public lookups that only have a tenant-agnostic
    key (e.g. a receipt's `qr_token`) and can't resolve the council any other way.
    Fine at this product's scale — six councils, worst case — see V2_ARCHITECTURE.md §1.
    """
    from apps.tenancy.models import Council

    for council in Council.objects.filter(is_active=True).order_by("id"):
        with council_context(council.id):
            result = query_fn(council)
        if result:
            return result
    return None
