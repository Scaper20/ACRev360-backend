from rest_framework import mixins, viewsets

from apps.accounts.models import AppRole
from apps.audit.api.serializers import AuditLogSerializer
from apps.audit.models import AuditLog
from apps.common.permissions import access_level_permission


class AuditLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Last 300 audit events — council admin only."""

    serializer_class = AuditLogSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]

    def get_queryset(self):
        return AuditLog.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")[:300]
