from django.db.models import Q
from django.http import Http404
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
class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Last 300 audit events. Council-tier: COUNCIL_ADMIN plus the
    RBAC-expansion COUNCIL_IGR_HEAD/COUNCIL_AUDITOR. Platform tier
    (council=null): COMPLIANCE_VIEW, EXTERNAL_AUDITOR (CouncilGrant-scoped,
    time-boxed), SUPER_ADMIN, PLATFORM_ADMIN — read across every council
    they're entitled to via apps.common.platform_scope, never an RLS bypass.
    See docs/RBAC_EXPANSION_DESIGN.md.

    RetrieveModelMixin added for the Reports page's Audit Log tab — its
    detail view needs GET /audit/{id} to show the full `detail` JSON blob a
    row's list serialization already carries but the list UI never surfaces
    (list truncates nothing, this is purely "click a row to look at its one
    field more closely," not a smaller list payload).

    get_object() does NOT use DRF's default (super().get_object(), which
    calls get_object_or_404(self.get_queryset(), pk=...)) for EITHER branch
    — get_queryset() always returns something already `[:300]`-sliced (a
    genuine queryset for single-council, a materialized list for
    platform-tier — see apps/common/api/views.py's module docstring for why
    the platform-tier branch specifically must be a list, not a lazy
    queryset, or RLS silently yields nothing), and Django raises
    `TypeError: Cannot filter a query once a slice has been taken` the
    moment get_object_or_404 tries `.get(pk=...)` on a sliced queryset —
    confirmed live via manage.py shell while building this, not just
    reasoned about. Retrieving one row by pk never needs the [:300] cap at
    all, so get_object() runs its own small unsliced query per branch
    instead of reusing get_queryset()."""

    serializer_class = AuditLogSerializer
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_AUDITOR,
        AppRole.COMPLIANCE_VIEW, AppRole.EXTERNAL_AUDITOR, AppRole.SUPER_ADMIN, AppRole.PLATFORM_ADMIN,
    )]
    lookup_value_regex = r"[0-9]+"

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

    def get_object(self):
        user = self.request.user
        pk = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)

        if user.council_id is not None:
            try:
                obj = AuditLog.objects.select_related("actor").get(council_id=user.council_id, pk=pk)
            except AuditLog.DoesNotExist as exc:
                raise Http404 from exc
            self.check_object_permissions(self.request, obj)
            return obj

        # Platform-tier: same reasoning as PlatformWideListMixin.get_object()
        # (apps/common/api/views.py) — walk each accessible council's own
        # RLS context and filter to this one pk there.
        from apps.common.platform_scope import platform_wide_queryset

        for row in platform_wide_queryset(
            lambda council_id: AuditLog.objects.filter(council_id=council_id, pk=pk).select_related("actor"),
            user,
        ):
            self.check_object_permissions(self.request, row)
            return row
        raise Http404
