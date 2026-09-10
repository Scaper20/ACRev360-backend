from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.audit.services import audit
from apps.billing.models import Bill
from apps.common.filtering import parse_int
from apps.common.permissions import access_level_permission
from apps.payments.models import Payment
from apps.settlements.api.serializers import (
    CommissionSettlementSerializer,
    ComputeSettlementsSerializer,
    MySettlementSummarySerializer,
    SettlementBillSerializer,
    SettlementStatusSerializer,
)
from apps.settlements.models import CommissionSettlement
from apps.settlements.services import compute_settlements


def _settlement_bill_rows(settlement: CommissionSettlement) -> list[dict]:
    """Per-bill breakdown behind one settlement's gross_collections: every
    bill of the consultant's payers with a confirmed payment inside the
    settlement's period, each bill's own collected-in-period amount, and its
    share of commission at the settlement's own (snapshotted) rate. Status is
    the settlement's — it describes the period, not any one bill in it."""
    bills = (
        Bill.objects.filter(
            council_id=settlement.council_id,
            payer__enumerated_by__consultant_id=settlement.consultant_id,
            payments__txn_status=Payment.CONFIRMED,
            payments__created_at__date__gte=settlement.period_start,
            payments__created_at__date__lte=settlement.period_end,
        )
        .distinct()
        .select_related("payer")
    )
    rows = []
    for bill in bills:
        collected = (
            bill.payments.filter(
                txn_status=Payment.CONFIRMED,
                created_at__date__gte=settlement.period_start,
                created_at__date__lte=settlement.period_end,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0")
        )
        commission = (collected * settlement.commission_rate / Decimal("100")).quantize(Decimal("0.01"))
        rows.append({
            "bill_id": bill.id,
            "bill_ref": bill.bill_ref,
            "payer_name": bill.payer.full_name,
            "collected": collected,
            "commission": commission,
            "status": settlement.status,
        })
    return rows


@extend_schema_view(
    list=extend_schema(parameters=[
        OpenApiParameter("q", OpenApiTypes.STR, description="Search by consultant name"),
        OpenApiParameter(
            "consultant_id", OpenApiTypes.INT,
            description="Filter by consultant. Narrows within the caller's own scope — it never widens it "
            "for a CONSULTANT/REVENUE_OFFICER, who only ever see their own settlements regardless.",
        ),
    ])
)
class CommissionSettlementViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CommissionSettlementSerializer
    # REVENUE_OFFICER included here (list only — `compute`/`status_change`
    # below already declare their own narrower COUNCIL_ADMIN-only
    # permission_classes) — a settlement is exactly the kind of "own
    # consultant's performance" data a revenue officer is meant to see.
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.REVENUE_OFFICER)]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        user = self.request.user
        qs = CommissionSettlement.objects.filter(council_id=user.council_id).order_by("-period_start")
        if user.access_level in (AppRole.CONSULTANT, AppRole.REVENUE_OFFICER):
            qs = qs.filter(consultant_id=user.consultant_id)
        else:
            consultant_id = parse_int(self.request.query_params, "consultant_id")
            if consultant_id is not None:
                qs = qs.filter(consultant_id=consultant_id)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(consultant__consultant_name__icontains=q)
        return qs

    @extend_schema(responses=SettlementBillSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="bills")
    def bills(self, request, pk=None):
        """Admin drill-down: one settlement's underlying bills, each with its
        own collected amount and commission share. Same scoping as list() —
        get_object() already applies get_queryset(), so a CONSULTANT/
        REVENUE_OFFICER can only drill into their own settlements."""
        settlement = self.get_object()
        return Response(SettlementBillSerializer(_settlement_bill_rows(settlement), many=True).data)

    @extend_schema(responses=MySettlementSummarySerializer)
    @action(detail=False, methods=["get"], url_path="my-summary")
    def my_summary(self, request):
        """Consultant's own dashboard: this-year/approved/settled commission
        totals plus their own per-bill list, across every settlement period
        that falls in the current year. A consultant with no settlements
        computed yet for this year gets zeros and an empty bill list —
        commission isn't official until compute_settlements() has run."""
        user = request.user
        if user.consultant_id is None:
            return Response({"error": "Not linked to a consultant."}, status=status.HTTP_400_BAD_REQUEST)

        year = timezone.localdate().year
        qs = CommissionSettlement.objects.filter(
            council_id=user.council_id, consultant_id=user.consultant_id, period_start__year=year,
        )
        total_this_year = qs.aggregate(total=Sum("commission_amount"))["total"] or Decimal("0")
        approved_total = (
            qs.filter(status=CommissionSettlement.APPROVED).aggregate(total=Sum("commission_amount"))["total"]
            or Decimal("0")
        )
        settled_total = (
            qs.filter(status=CommissionSettlement.SETTLED).aggregate(total=Sum("commission_amount"))["total"]
            or Decimal("0")
        )

        bills = []
        for settlement in qs:
            bills.extend(_settlement_bill_rows(settlement))

        return Response(MySettlementSummarySerializer({
            "total_this_year": total_this_year,
            "approved_total": approved_total,
            "settled_total": settled_total,
            "bills": bills,
        }).data)

    @extend_schema(request=ComputeSettlementsSerializer, responses=CommissionSettlementSerializer(many=True))
    @action(detail=False, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def compute(self, request):
        serializer = ComputeSettlementsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = compute_settlements(
            council_id=request.user.council_id, actor=request.user, **serializer.validated_data
        )
        return Response(CommissionSettlementSerializer(results, many=True).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=SettlementStatusSerializer, responses=CommissionSettlementSerializer)
    @action(detail=True, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def status_change(self, request, pk=None):
        settlement = self.get_object()
        serializer = SettlementStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_status = settlement.status
        settlement.status = serializer.validated_data["status"]
        settlement.save(update_fields=["status", "updated_at"])
        audit(
            council_id=settlement.council_id, actor=request.user, action="SETTLEMENT_STATUS_CHANGED",
            entity_type="COMMISSION_SETTLEMENT", entity_id=settlement.id,
            detail={"old_status": old_status, "new_status": settlement.status},
        )
        return Response(CommissionSettlementSerializer(settlement).data)
