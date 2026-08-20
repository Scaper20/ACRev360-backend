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
    """Last 300 audit events — council admin only."""

    serializer_class = AuditLogSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]

    def get_queryset(self):
        # `q` must filter before the [:300] slice — a sliced queryset can't
        # be filtered further (Django raises on it).
        qs = AuditLog.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(actor__username__icontains=q) | Q(action__icontains=q) | Q(entity_type__icontains=q))
        return qs[:300]
