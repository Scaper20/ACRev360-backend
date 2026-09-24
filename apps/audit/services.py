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


def audit_user_event(*, user, action, detail=None, actor_ip=None):
    """Record something a user did to their own account (login, password change,
    email change). Audit rows belong to a council (audit_log.council is NOT NULL
    and row-level-secured), so a platform-tier user — council=null — has nowhere
    to be recorded and is skipped rather than crashing the request that was
    otherwise fine. Returns whether a row was written."""
    if user is None or user.council_id is None:
        return False
    audit(
        council_id=user.council_id, actor=user, action=action, entity_type="APP_USER",
        entity_id=user.id, detail=detail, actor_ip=actor_ip,
    )
    return True
