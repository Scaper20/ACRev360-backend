from apps.audit.models import AuditLog


def audit(*, council_id, actor, action, entity_type, entity_id, detail=None, actor_ip=None):
    AuditLog.objects.create(
        council_id=council_id,
        actor=actor,
        actor_ip=actor_ip,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        detail=detail or {},
    )
