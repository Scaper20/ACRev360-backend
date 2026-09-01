from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.enforcement.api.serializers import DebtCaseSerializer
from apps.enforcement.models import DebtCase
from apps.enforcement.services import escalate, refresh_debt


class DebtRefreshResponseSerializer(serializers.Serializer):
    opened = serializers.IntegerField()
    updated = serializers.IntegerField()


@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="Search by bill reference or payer name")])
)
class DebtCaseViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = DebtCaseSerializer
    # ListModelMixin only (no create) — `refresh`/`escalate` below already
    # declare their own narrower COUNCIL_ADMIN-only permission_classes, so
    # REVENUE_OFFICER landing here only ever reaches `list`, scoped the same
    # as CONSULTANT via common.scoping.portfolio_filter.
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.REVENUE_OFFICER)]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        qs = DebtCase.objects.filter(council_id=self.request.user.council_id).order_by("-opened_at")
        qs = portfolio_filter(qs, self.request, payer_path="bill__payer")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(bill__bill_ref__icontains=q) | Q(bill__payer__full_name__icontains=q))
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
