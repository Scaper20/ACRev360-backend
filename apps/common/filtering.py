"""
Shared query-param parsing for the list endpoints' optional filters, plus a
pagination-stable ordering backend.

Why parse rather than pass a raw param straight into `.filter()`: Django raises a
plain `ValueError` ("Field 'id' expected a number but got 'abc'") or a
`django.core.exceptions.ValidationError` for a malformed id/date, and neither is a
DRF `APIException` — so it skips `apps.common.exceptions.acrev360_exception_handler`
entirely and falls through to Django's generic 500, with no detail to the client and
(DEBUG off in this project's actual deployment) nothing in the logs either. Same
class of problem as the uncaught `IntegrityError` already documented in
docs/CHANGELOG.md. Every optional filter goes through these helpers so a bad value
is a 400 naming the offending param instead.
"""
import datetime
from decimal import Decimal, InvalidOperation

from rest_framework import serializers
from rest_framework.filters import OrderingFilter


def parse_int(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise serializers.ValidationError({name: "Must be an integer."})


def parse_date(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise serializers.ValidationError({name: "Must be an ISO date (YYYY-MM-DD)."})


def parse_decimal(params, name):
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise serializers.ValidationError({name: "Must be a number."})


def apply_payer_dimension_filters(qs, params, *, payer_path=""):
    """`ward_id` / `consultant_id`, expressed relative to whichever model in the
    queryset reaches `Payer`.

    `payer_path=""` when Payer is the queryset's own model (PayerViewSet),
    `"payer"` when it's one hop away (BillViewSet) — the same convention
    `common.scoping.portfolio_filter` already uses, and the same
    `enumerated_by__consultant_id` join it scopes by.

    Deliberately a *narrowing* filter layered on top of `portfolio_filter`, never a
    replacement for it: a CONSULTANT/REVENUE_OFFICER passing another firm's
    `consultant_id` still gets the intersection of the two (empty), not that firm's
    rows. Don't reorder these so this runs without portfolio_filter also applied.
    """
    prefix = f"{payer_path}__" if payer_path else ""

    ward_id = parse_int(params, "ward_id")
    if ward_id is not None:
        qs = qs.filter(**{f"{prefix}ward_id": ward_id})

    consultant_id = parse_int(params, "consultant_id")
    if consultant_id is not None:
        qs = qs.filter(**{f"{prefix}enumerated_by__consultant_id": consultant_id})

    return qs


def apply_date_range(qs, params, *, field):
    """`date_from`/`date_to` against a DateTimeField, inclusive on both ends."""
    date_from = parse_date(params, "date_from")
    if date_from is not None:
        qs = qs.filter(**{f"{field}__date__gte": date_from})
    date_to = parse_date(params, "date_to")
    if date_to is not None:
        qs = qs.filter(**{f"{field}__date__lte": date_to})
    return qs


class StableOrderingFilter(OrderingFilter):
    """DRF's OrderingFilter plus a deterministic `pk` tiebreaker.

    Sorting a *paginated* list by a non-unique column (`total_amount`, `due_date`
    and `full_name` all tie in real data) leaves tied rows in whatever order
    Postgres happens to return, and that order can differ between the request for
    page 1 and the request for page 2 — so a row silently repeats on one page and
    never appears on the other. Appending `pk` makes the sort total.

    Ordering is otherwise unchanged from DRF's: an absent or unrecognised
    `ordering` value leaves the queryset's own `.order_by()` in place rather than
    erroring (`remove_invalid_fields` drops anything not in `ordering_fields`).
    """

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset
        return queryset.order_by(*ordering, "pk")
