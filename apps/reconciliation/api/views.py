from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.common.filtering import StableOrderingFilter, parse_date
from apps.common.permissions import access_level_permission
from apps.payments.models import PaymentChannel
from apps.reconciliation.api.serializers import (
    GlobalExceptionSerializer,
    LiveSummarySerializer,
    ReconciliationRunSerializer,
    ResolveExceptionSerializer,
    RunReconciliationSerializer,
)
from apps.reconciliation.models import ReconciliationException, ReconciliationRun
from apps.reconciliation.services import ReconciliationError, live_reconciliation_summary, run_reconciliation


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("channel", OpenApiTypes.STR, description="Filter by channel code"),
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by status — OPEN, BALANCED, EXCEPTIONS, CLOSED"),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Only runs on/after this run_date"),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Only runs on/before this run_date"),
        ]
    )
)
class ReconciliationRunViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """COUNCIL_IGR_HEAD and COUNCIL_TREASURY (docs/RBAC_EXPANSION_DESIGN.md)
    get the same run/resolve rights as COUNCIL_ADMIN here — reconciling
    remittances is literally their job per the matrix, unlike every other
    viewset those two roles only read. COUNCIL_AUDITOR stays read-only.

    CONSULTANT deliberately excluded (confirmed with the client, 2026-09):
    every figure this viewset and its live-summary/exceptions actions expose
    (total_platform, total_bank, unmatched credits) is a whole-council
    bank-vs-platform match for a channel/day — get_queryset() below only
    ever filtered by council_id, never by consultant, because there is no
    per-consultant reading of "does the bank statement match the platform"
    to filter down to; a run reconciles the entire day's feed against the
    entire platform, not one consultant's slice of it. A freshly onboarded
    consultant with zero payments of their own was seeing the exact same
    council-wide total as everyone else, which read as a data leak (and
    would have kept doing so at any consultant-scoped total, since there
    genuinely isn't one). This is finance/admin-tier data, not portfolio
    data — unlike Payment/Receipt/POSTerminal, it was never a scoping bug
    to fix, it was a permission grant that should not have been there."""

    serializer_class = ReconciliationRunSerializer
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY,
        AppRole.COUNCIL_AUDITOR,
    )]
    # All real ReconciliationRun columns — unlike ReceiptViewSet/DebtCaseViewSet,
    # no annotation needed here (no serializer-only alias among these).
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["run_date", "total_platform", "total_bank", "status"]

    def get_queryset(self):
        # ReconciliationRunSerializer walks channel.code plus a nested
        # exceptions list that each read feed_row.bank_txn_ref/amount — the
        # select_related + Prefetch keep the run list at 3 queries total
        # instead of 1 per run + 2 per exception (PERF-3).
        qs = (
            ReconciliationRun.objects.filter(council_id=self.request.user.council_id)
            .select_related("channel")
            .prefetch_related(
                Prefetch(
                    "exceptions",
                    queryset=ReconciliationException.objects.select_related("feed_row"),
                )
            )
            .order_by("-run_date")
        )
        channel = self.request.query_params.get("channel")
        if channel:
            qs = qs.filter(channel__code=channel)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        # run_date is a plain DateField, not a timestamp — unlike
        # PaymentViewSet/ReceiptViewSet's created_at, there's no time-of-day
        # component to worry about, so a direct gte/lte date comparison is
        # already sargable against the column's own b-tree with no __date
        # cast or timezone-aware span-bounds helper needed (date_span_bounds
        # is explicitly for a DateTimeField — see its own docstring).
        date_from = parse_date(self.request.query_params, "date_from")
        if date_from is not None:
            qs = qs.filter(run_date__gte=date_from)
        date_to = parse_date(self.request.query_params, "date_to")
        if date_to is not None:
            qs = qs.filter(run_date__lte=date_to)
        return qs

    @extend_schema(request=RunReconciliationSerializer, responses=ReconciliationRunSerializer)
    @action(
        detail=False, methods=["post"],
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY)],
    )
    def run(self, request):
        serializer = RunReconciliationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel, _ = PaymentChannel.objects.get_or_create(code=serializer.validated_data["channel_code"])
        try:
            recon = run_reconciliation(
                council_id=request.user.council_id, channel=channel,
                run_date=serializer.validated_data["date"], actor=request.user,
            )
        except ReconciliationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ReconciliationRunSerializer(recon).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("exception_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=ResolveExceptionSerializer, responses=ReconciliationRunSerializer,
    )
    @action(
        detail=False, methods=["post"], url_path=r"exceptions/(?P<exception_id>[0-9]+)/resolve",
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY)],
    )
    def resolve_exception(self, request, exception_id=None):
        exception = get_object_or_404(ReconciliationException, pk=exception_id, council_id=request.user.council_id)
        serializer = ResolveExceptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        exception.note = serializer.validated_data["note"]
        exception.resolved_at = timezone.now()
        exception.resolved_by = request.user
        exception.save(update_fields=["note", "resolved_at", "resolved_by"])
        return Response(ReconciliationRunSerializer(exception.run).data)

    @extend_schema(
        parameters=[OpenApiParameter("date", OpenApiTypes.DATE, OpenApiParameter.QUERY, required=False)],
        responses=LiveSummarySerializer,
    )
    @action(detail=False, methods=["get"], url_path="live-summary")
    def live_summary(self, request):
        """Always-current dashboard view alongside the manual `run` action
        above — computed fresh on every call, no ReconciliationRun triggered
        or required. Defaults to today; ?date=YYYY-MM-DD for any other day."""
        run_date = parse_date(request.query_params, "date")
        summary = live_reconciliation_summary(council_id=request.user.council_id, run_date=run_date)
        return Response(LiveSummarySerializer(summary).data)

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
