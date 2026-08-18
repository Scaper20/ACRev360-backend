from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.common.permissions import access_level_permission
from apps.payments.models import PaymentChannel
from apps.reconciliation.api.serializers import (
    GlobalExceptionSerializer,
    ReconciliationRunSerializer,
    ResolveExceptionSerializer,
    RunReconciliationSerializer,
)
from apps.reconciliation.models import ReconciliationException, ReconciliationRun
from apps.reconciliation.services import run_reconciliation


class ReconciliationRunViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ReconciliationRunSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)]

    def get_queryset(self):
        return ReconciliationRun.objects.filter(council_id=self.request.user.council_id).order_by("-run_date")

    @extend_schema(request=RunReconciliationSerializer, responses=ReconciliationRunSerializer)
    @action(detail=False, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def run(self, request):
        serializer = RunReconciliationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel, _ = PaymentChannel.objects.get_or_create(code=serializer.validated_data["channel_code"])
        recon = run_reconciliation(
            council_id=request.user.council_id, channel=channel,
            run_date=serializer.validated_data["date"], actor=request.user,
        )
        return Response(ReconciliationRunSerializer(recon).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("exception_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=ResolveExceptionSerializer, responses=ReconciliationRunSerializer,
    )
    @action(
        detail=False, methods=["post"], url_path=r"exceptions/(?P<exception_id>[0-9]+)/resolve",
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)],
    )
    def resolve_exception(self, request, exception_id=None):
        exception = ReconciliationException.objects.get(pk=exception_id, council_id=request.user.council_id)
        serializer = ResolveExceptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exception.note = serializer.validated_data["note"]
        exception.resolved_at = timezone.now()
        exception.resolved_by = request.user
        exception.save(update_fields=["note", "resolved_at", "resolved_by"])
        return Response(ReconciliationRunSerializer(exception.run).data)

    @extend_schema(
        parameters=[OpenApiParameter("resolved", OpenApiTypes.BOOL, OpenApiParameter.QUERY, required=False)],
        responses=GlobalExceptionSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_path="exceptions")
    def exceptions(self, request):
        """Cross-run view of exceptions — 'browse everything unmatched' rather than
        having to already know which run an exception belongs to. Defaults to
        unresolved-only; ?resolved=true/false narrows either way."""
        qs = ReconciliationException.objects.filter(council_id=request.user.council_id).select_related(
            "run", "run__channel", "feed_row"
        ).order_by("-run__run_date")
        resolved_param = request.query_params.get("resolved")
        if resolved_param is None:
            qs = qs.filter(resolved_at__isnull=True)
        else:
            want_resolved = resolved_param.lower() == "true"
            qs = qs.filter(resolved_at__isnull=not want_resolved)
        return Response(GlobalExceptionSerializer(qs, many=True).data)
