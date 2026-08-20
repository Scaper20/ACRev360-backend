from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole, FieldAgent
from apps.common.permissions import access_level_permission
from apps.fieldops.api.serializers import SyncRequestSerializer, SyncResponseSerializer, WorklistPayerSerializer
from apps.fieldops.services import get_worklist, replay_sync_record


def _agent_for(request):
    return get_object_or_404(FieldAgent, user_id=request.user.id, council_id=request.user.council_id)


@extend_schema_view(
    get=extend_schema(parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="Search by payer name or reference")])
)
class WorklistView(generics.ListAPIView):
    """The agent's own ward-scoped payer list — see fieldops.services.get_worklist."""

    serializer_class = WorklistPayerSerializer
    permission_classes = [access_level_permission(AppRole.AGENT)]

    def get_queryset(self):
        agent = _agent_for(self.request)
        return get_worklist(council_id=self.request.user.council_id, agent=agent, q=self.request.query_params.get("q"))


class SyncView(APIView):
    """Batch-replays offline-queued PAYMENT/PAYER records — see
    fieldops.services.replay_sync_record for the idempotency and per-record
    error handling this wraps."""

    permission_classes = [access_level_permission(AppRole.AGENT)]

    @extend_schema(request=SyncRequestSerializer, responses=SyncResponseSerializer)
    def post(self, request):
        agent = _agent_for(request)
        serializer = SyncRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = {"accepted": [], "conflicts": [], "rejected": []}
        for record in serializer.validated_data["records"]:
            bucket, sync_record = replay_sync_record(
                council_id=request.user.council_id, agent=agent, actor=request.user,
                client_id=record["client_id"], record_type=record["entity_type"], payload=record["payload"],
            )
            result[bucket].append({
                "client_id": sync_record.client_id,
                "result_ref": sync_record.result_ref,
                "detail": sync_record.detail,
            })
        return Response(result, status=status.HTTP_200_OK)
