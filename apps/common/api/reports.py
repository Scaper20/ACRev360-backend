"""
Ad-hoc report endpoint — GET /api/v1/reports. Scoped in conversation to four
entities (Payers/Bills/Payments/Settlements) and four dimensions (ward,
revenue_item, consultant, date) — item 4 of the frontend's backend
requirements doc.

Deliberately not a fully free-form query builder (arbitrary joins/fields
picked by the client) — that's a much bigger, riskier surface (dynamic ORM
construction from untrusted input). This is "ad-hoc" within a fixed,
validated combinatorial space instead: any entity x up to 2 of its allowed
dimensions x its allowed filters, returned as already-aggregated rows (counts
and sums), never raw identity-level records — matching how every other list
endpoint in this codebase already takes filters through query params, and
never exposing more than that entity's own direct endpoint already does.

Per-entity access mirrors that entity's own viewset exactly rather than
inventing a new visibility rule: PayerViewSet/BillViewSet/PaymentViewSet
exclude GLOBAL_VIEW (payer/bill identity), so this does too; CommissionSettlement
excludes AGENT, matching CommissionSettlementViewSet. CONSULTANT/REVENUE_OFFICER
are scoped to their own portfolio the same way as everywhere else (see
common.scoping.portfolio_filter; settlements has its own direct consultant_id
filter since it has no payer to walk through).
"""
import datetime

from django.db.models import Count, Exists, F, OuterRef, Sum
from django.db.models.functions import TruncDate
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.billing.models import Bill, BillLine
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.payments.models import Payment
from apps.registry.models import Payer
from apps.settlements.models import CommissionSettlement

PAYERS, BILLS, PAYMENTS, SETTLEMENTS = "PAYERS", "BILLS", "PAYMENTS", "SETTLEMENTS"
WARD, REVENUE_ITEM, CONSULTANT, DATE = "ward", "revenue_item", "consultant", "date"

_ENTITY_LEVELS = {
    PAYERS: (AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER),
    BILLS: (AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER),
    PAYMENTS: (AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER),
    SETTLEMENTS: (AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.REVENUE_OFFICER),
}
_ENTITY_DIMENSIONS = {
    PAYERS: (WARD, CONSULTANT, DATE),
    BILLS: (WARD, CONSULTANT, REVENUE_ITEM, DATE),
    PAYMENTS: (WARD, CONSULTANT, DATE),
    SETTLEMENTS: (CONSULTANT, DATE),
}
_ALL_LEVELS = sorted({level for levels in _ENTITY_LEVELS.values() for level in levels})


def _money(value):
    return f"{value or 0:.2f}"


def _format_row(labels, measures, rename, label_defaults):
    out = {}
    for path, value in labels.items():
        name = rename[path]
        if name == "date":
            out[name] = value.isoformat() if value else None
        else:
            out[name] = value if value is not None else label_defaults.get(name)
    for name, value in measures.items():
        out[name] = value if name == "count" else _money(value)
    return out


def _aggregate(qs, group_by, field_map, *, date_field, aggregates, label_defaults=None):
    label_defaults = label_defaults or {}
    values_fields = []
    rename = {}

    if DATE in group_by:
        qs = qs.annotate(__date=TruncDate(date_field))
        values_fields.append("__date")
        rename["__date"] = "date"
    for dim in group_by:
        if dim == DATE:
            continue
        path = field_map[dim]
        values_fields.append(path)
        rename[path] = dim

    if not group_by:
        return [_format_row({}, qs.aggregate(**aggregates), rename, label_defaults)]

    rows = qs.values(*values_fields).annotate(**aggregates).order_by()
    result = []
    for row in rows:
        labels = {k: v for k, v in row.items() if k in values_fields}
        measures = {k: v for k, v in row.items() if k not in values_fields}
        result.append(_format_row(labels, measures, rename, label_defaults))
    return result


def _apply_common_filters(qs, f, *, ward_field, consultant_field, date_field):
    if ward_field and f.get("ward_id"):
        qs = qs.filter(**{ward_field: f["ward_id"]})
    if consultant_field and f.get("consultant_id"):
        qs = qs.filter(**{consultant_field: f["consultant_id"]})
    if f.get("date_from"):
        qs = qs.filter(**{f"{date_field}__date__gte": f["date_from"]})
    if f.get("date_to"):
        qs = qs.filter(**{f"{date_field}__date__lte": f["date_to"]})
    return qs


def _payers_report(request, group_by, f):
    qs = Payer.objects.filter(council_id=request.user.council_id)
    qs = portfolio_filter(qs, request, payer_path="")
    qs = _apply_common_filters(qs, f, ward_field="ward_id", consultant_field="enumerated_by__consultant_id", date_field="created_at")

    field_map = {WARD: "ward__ward_name", CONSULTANT: "enumerated_by__consultant__consultant_name"}
    return _aggregate(
        qs, group_by, field_map, date_field="created_at",
        aggregates={"count": Count("id", distinct=True)}, label_defaults={CONSULTANT: "Council Direct"},
    )


def _bills_report(request, group_by, f):
    qs = Bill.objects.filter(council_id=request.user.council_id).exclude(status__in=[Bill.SUPERSEDED, Bill.CANCELLED])
    qs = portfolio_filter(qs, request)
    qs = _apply_common_filters(qs, f, ward_field="payer__ward_id", consultant_field="payer__enumerated_by__consultant_id", date_field="created_at")

    field_map = {WARD: "payer__ward__ward_name", CONSULTANT: "payer__enumerated_by__consultant__consultant_name"}

    if REVENUE_ITEM in group_by:
        # Fans out per BillLine rather than per Bill — a multi-item bill has
        # no single "the" item, so this switches both the base rows and the
        # money measure to line_amount, matching DashboardSummaryView's own
        # by_item breakdown (apps/common/api/dashboard.py) rather than
        # inventing a different convention here. Per-line sums are unaffected
        # by the join's row multiplication, so the plain join filter is correct
        # here (and keeps "filter to item X, grouped by item" showing only X).
        qs = qs.exclude(lines__isnull=True)
        if f.get("revenue_item_id"):
            qs = qs.filter(lines__assessment__council_revenue_item_id=f["revenue_item_id"])
        field_map = dict(field_map, revenue_item="lines__assessment__council_revenue_item__item_name")
        aggregates = {"count": Count("id", distinct=True), "billed": Sum("lines__line_amount")}
    else:
        if f.get("revenue_item_id"):
            # Exists(), not a join filter: the measures below sum *bill-level*
            # columns, and a join to lines duplicates the bill row once per
            # matching line — a bill carrying two lines for the same item under
            # different rate bands (add_bill_line merges only when item+band+
            # tier all match) would have its total counted twice. Count() was
            # already distinct-protected; the Sums were not.
            qs = qs.filter(
                Exists(BillLine.objects.filter(bill=OuterRef("pk"), assessment__council_revenue_item_id=f["revenue_item_id"]))
            )
        aggregates = {
            "count": Count("id", distinct=True),
            "billed": Sum(F("total_amount") - F("arrears_amount")),
            "arrears": Sum("arrears_amount"),
            "balance": Sum(F("total_amount") - F("amount_paid")),
        }
    return _aggregate(qs, group_by, field_map, date_field="created_at", aggregates=aggregates, label_defaults={CONSULTANT: "Council Direct"})


def _payments_report(request, group_by, f):
    qs = Payment.objects.filter(council_id=request.user.council_id, txn_status=Payment.CONFIRMED)
    qs = portfolio_filter(qs, request, payer_path="bill__payer")
    qs = _apply_common_filters(qs, f, ward_field="bill__payer__ward_id", consultant_field="bill__payer__enumerated_by__consultant_id", date_field="created_at")

    field_map = {WARD: "bill__payer__ward__ward_name", CONSULTANT: "bill__payer__enumerated_by__consultant__consultant_name"}
    return _aggregate(
        qs, group_by, field_map, date_field="created_at",
        aggregates={"count": Count("id", distinct=True), "amount": Sum("amount")}, label_defaults={CONSULTANT: "Council Direct"},
    )


def _settlements_report(request, group_by, f):
    # No payer to walk through (unlike the other three) — scoped directly off
    # consultant_id, same as CommissionSettlementViewSet.get_queryset.
    qs = CommissionSettlement.objects.filter(council_id=request.user.council_id)
    if request.user.access_level in (AppRole.CONSULTANT, AppRole.REVENUE_OFFICER):
        qs = qs.filter(consultant_id=request.user.consultant_id)
    if f.get("consultant_id"):
        qs = qs.filter(consultant_id=f["consultant_id"])
    if f.get("date_from"):
        qs = qs.filter(period_start__gte=f["date_from"])
    if f.get("date_to"):
        qs = qs.filter(period_start__lte=f["date_to"])

    field_map = {CONSULTANT: "consultant__consultant_name"}
    return _aggregate(
        qs, group_by, field_map, date_field="period_start",
        aggregates={"count": Count("id", distinct=True), "commission_amount": Sum("commission_amount"), "gross_collections": Sum("gross_collections")},
    )


_BUILDERS = {PAYERS: _payers_report, BILLS: _bills_report, PAYMENTS: _payments_report, SETTLEMENTS: _settlements_report}

_ReportResponseSerializer = inline_serializer(
    "ReportResponse",
    {
        "entity": serializers.CharField(),
        "group_by": serializers.ListField(child=serializers.CharField()),
        "rows": serializers.ListField(child=serializers.DictField()),
    },
)


class ReportsView(APIView):
    permission_classes = [access_level_permission(*_ALL_LEVELS)]

    @extend_schema(
        parameters=[
            OpenApiParameter("entity", OpenApiTypes.STR, required=True, description="PAYERS | BILLS | PAYMENTS | SETTLEMENTS"),
            OpenApiParameter("group_by", OpenApiTypes.STR, description="Repeatable, max 2 — ward, revenue_item, consultant, date"),
            OpenApiParameter("date_from", OpenApiTypes.DATE),
            OpenApiParameter("date_to", OpenApiTypes.DATE),
            OpenApiParameter("ward_id", OpenApiTypes.INT),
            OpenApiParameter("consultant_id", OpenApiTypes.INT),
            OpenApiParameter("revenue_item_id", OpenApiTypes.INT, description="BILLS only"),
        ],
        responses=_ReportResponseSerializer,
        tags=["reports"],
    )
    def get(self, request):
        entity = request.query_params.get("entity", "").upper()
        if entity not in _ENTITY_LEVELS:
            return Response({"error": f"entity must be one of {', '.join(_ENTITY_LEVELS)}"}, status=status.HTTP_400_BAD_REQUEST)
        if request.user.access_level not in _ENTITY_LEVELS[entity]:
            return Response({"error": "Not permitted for this entity"}, status=status.HTTP_403_FORBIDDEN)

        group_by = request.query_params.getlist("group_by")
        if len(group_by) > 2:
            return Response({"error": "group_by accepts at most 2 dimensions"}, status=status.HTTP_400_BAD_REQUEST)
        allowed_dims = _ENTITY_DIMENSIONS[entity]
        for dim in group_by:
            if dim not in allowed_dims:
                return Response(
                    {"error": f"'{dim}' is not a supported dimension for {entity} — choose from {', '.join(allowed_dims)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        params = request.query_params
        f = {
            "date_from": params.get("date_from"),
            "date_to": params.get("date_to"),
            "ward_id": params.get("ward_id"),
            "consultant_id": params.get("consultant_id"),
            "revenue_item_id": params.get("revenue_item_id"),
        }
        if f["revenue_item_id"] and entity != BILLS:
            return Response({"error": "revenue_item_id only applies to BILLS"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            for key in ("date_from", "date_to"):
                if f[key]:
                    datetime.date.fromisoformat(f[key])
        except ValueError:
            return Response({"error": f"{key} must be an ISO date (YYYY-MM-DD)"}, status=status.HTTP_400_BAD_REQUEST)

        rows = _BUILDERS[entity](request, group_by, f)
        return Response({"entity": entity, "group_by": group_by, "rows": rows})
