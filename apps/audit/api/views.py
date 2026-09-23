from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets

from apps.accounts.models import AppRole
from apps.audit.api.serializers import AuditLogSerializer
from apps.audit.models import AuditLog
from apps.common.permissions import access_level_permission


@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="Search by actor username, action or entity type")])
)
class AuditLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Last 300 audit events. Council-tier: COUNCIL_ADMIN plus the
    RBAC-expansion COUNCIL_IGR_HEAD/COUNCIL_AUDITOR. Platform tier
    (council=null): COMPLIANCE_VIEW, EXTERNAL_AUDITOR (CouncilGrant-scoped,
    time-boxed), SUPER_ADMIN, PLATFORM_ADMIN — read across every council
    they're entitled to via apps.common.platform_scope, never an RLS bypass.
    See docs/RBAC_EXPANSION_DESIGN.md."""

    serializer_class = AuditLogSerializer
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_AUDITOR,
        AppRole.COMPLIANCE_VIEW, AppRole.EXTERNAL_AUDITOR, AppRole.SUPER_ADMIN, AppRole.PLATFORM_ADMIN,
    )]

    def get_queryset(self):
        user = self.request.user
        q = self.request.query_params.get("q")

        if user.council_id is None:
            from apps.common.platform_scope import platform_wide_queryset

            def per_council(council_id):
                # AuditLogSerializer walks actor.username — select_related
                # keeps the last-300 log at one query per council (PERF-3).
                inner = AuditLog.objects.filter(council_id=council_id).select_related("actor").order_by("-created_at")
                if q:
                    inner = inner.filter(Q(actor__username__icontains=q) | Q(action__icontains=q) | Q(entity_type__icontains=q))
                return inner[:300]

            rows = platform_wide_queryset(per_council, user)
            rows.sort(key=lambda row: row.created_at, reverse=True)
            return rows[:300]

        # `q` must filter before the [:300] slice — a sliced queryset can't
        # be filtered further (Django raises on it).
        qs = AuditLog.objects.filter(council_id=user.council_id).select_related("actor").order_by("-created_at")
        if q:
            qs = qs.filter(Q(actor__username__icontains=q) | Q(action__icontains=q) | Q(entity_type__icontains=q))
        return qs[:300]
