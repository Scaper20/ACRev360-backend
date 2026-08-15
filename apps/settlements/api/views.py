from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.audit.services import audit
from apps.common.permissions import access_level_permission
from apps.settlements.api.serializers import (
    CommissionSettlementSerializer,
    ComputeSettlementsSerializer,
    SettlementStatusSerializer,
)
from apps.settlements.models import CommissionSettlement
from apps.settlements.services import compute_settlements


class CommissionSettlementViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CommissionSettlementSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)]

    def get_queryset(self):
        user = self.request.user
        qs = CommissionSettlement.objects.filter(council_id=user.council_id).order_by("-period_start")
        if user.access_level == AppRole.CONSULTANT:
            qs = qs.filter(consultant_id=user.consultant_id)
        return qs

    @action(detail=False, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def compute(self, request):
        serializer = ComputeSettlementsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = compute_settlements(
            council_id=request.user.council_id, actor=request.user, **serializer.validated_data
        )
        return Response(CommissionSettlementSerializer(results, many=True).data, status=status.HTTP_201_CREATED)

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
