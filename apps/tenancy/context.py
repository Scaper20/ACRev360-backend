"""
Helpers for setting the Postgres session-local tenant context that RLS policies
check (`current_setting('app.council_id', true)`). See V2_ARCHITECTURE.md §3.

Two ways this gets set:
  1. Per-request, for authenticated calls — apps.tenancy.middleware.CouncilContextMiddleware
     sets it once for the whole request from the JWT's `council_id` claim.
  2. Explicitly, for the handful of public/anonymous endpoints (bill lookup, receipt
     verification) that resolve their own target council from the request itself
     (a bill_ref's prefix, or by trying each active council) rather than from a user.
"""
from contextlib import contextmanager

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


def resolve_council_from_bill_ref(bill_ref: str):
    """A bill_ref (`KAC/2026/000123`) embeds its council's configured prefix — so
    public/pre-auth callers (bill lookup, channel webhooks) can resolve the target
    council from the reference itself, no bypass needed. See apps/billing and
    apps/channels public views."""
    prefix = bill_ref.split("/")[0] if bill_ref else None
    if not prefix:
        return None

    def lookup(council):
        config = getattr(council, "config", None)
        return council if config and config.bill_ref_prefix == prefix else None

    return find_across_active_councils(lookup)


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
