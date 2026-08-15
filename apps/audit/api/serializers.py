from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "action", "entity_type", "entity_id", "detail", "actor", "actor_username", "actor_ip", "created_at"]
        read_only_fields = fields
