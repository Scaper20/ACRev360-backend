from django.db import models

from apps.tenancy.models import CouncilScopedModel


class AuditLog(CouncilScopedModel):
    """Append-only. Every state-changing action of consequence gets one row — who,
    what, when, and, where relevant, the before/after detail. Written from inside
    the service function that performed the action (apps/*/services.py), not from
    views — see V2_ARCHITECTURE.md §7 and TDD.md §4.7."""

    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.CharField(max_length=64)
    detail = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        "accounts.AppUser", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events"
    )
    actor_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["council", "entity_type", "entity_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.entity_type}:{self.entity_id}"
