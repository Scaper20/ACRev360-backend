from django.db.models import F, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.common.filtering import StableOrderingFilter, name_search_q
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.enforcement.api.serializers import DebtCaseSerializer
from apps.enforcement.models import DebtCase
from apps.enforcement.services import escalate, refresh_debt


class DebtRefreshResponseSerializer(serializers.Serializer):
    opened = serializers.IntegerField()
    updated = serializers.IntegerField()


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by bill reference or payer name"),
            OpenApiParameter("ageing_bucket", OpenApiTypes.STR, description="Filter by ageing bucket — 0_30, 31_60, 61_90, OVER_90"),
            OpenApiParameter(
                "enforcement_stage", OpenApiTypes.STR,
                description="Filter by enforcement stage — NONE, FIRST_NOTICE, FINAL_NOTICE, ENFORCEMENT, LEGAL, CLOSED",
            ),
        ]
    )
)
class DebtCaseViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = DebtCaseSerializer
    # ListModelMixin only (no create) — `refresh`/`escalate` below already
    # declare their own narrower COUNCIL_ADMIN-only permission_classes, so
    # every level here (including the RBAC-expansion additions — see
    # docs/RBAC_EXPANSION_DESIGN.md) only ever reaches `list`, scoped the
    # same as CONSULTANT via common.scoping.portfolio_filter.
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.REVENUE_OFFICER,
        AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR, AppRole.CONSULTANT_STAFF,
    )]
    lookup_value_regex = r"[0-9]+"
    # Same StableOrderingFilter shape as ReceiptViewSet — see that file's
    # comment on why "balance" needs the matching annotation below rather
    # than just being listed here. DebtCase.opened_at and reminder_count are
    # real columns; balance is DebtCaseSerializer's own alias for
    # bill.balance, itself a Python @property (bill.total_amount -
    # bill.amount_paid), not a database column the ORM could order by
    # directly even via a bill__balance path.
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["opened_at", "reminder_count", "balance"]

    def get_queryset(self):
        # DebtCaseSerializer reads bill.bill_ref, bill.payer.full_name and
        # bill.balance per row.
        qs = (
            DebtCase.objects.filter(council_id=self.request.user.council_id)
            .select_related("bill__payer")
            .annotate(balance=F("bill__total_amount") - F("bill__amount_paid"))
            .order_by("-opened_at")
        )
        qs = portfolio_filter(qs, self.request, payer_path="bill__payer")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(bill__bill_ref__icontains=q) | name_search_q(q, prefix="bill__payer"))
        ageing_bucket = self.request.query_params.get("ageing_bucket")
        if ageing_bucket:
            qs = qs.filter(ageing_bucket=ageing_bucket)
        enforcement_stage = self.request.query_params.get("enforcement_stage")
        if enforcement_stage:
            qs = qs.filter(enforcement_stage=enforcement_stage)
        return qs

    @extend_schema(request=None, responses=DebtRefreshResponseSerializer)
    @action(detail=False, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def refresh(self, request):
        result = refresh_debt(council_id=request.user.council_id, actor=request.user)
        return Response(result)

    @extend_schema(request=None, responses=DebtCaseSerializer)
    @action(detail=True, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def escalate(self, request, pk=None):
        case = self.get_object()
        try:
            escalate(debt_case=case, actor=request.user)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DebtCaseSerializer(case).data)
