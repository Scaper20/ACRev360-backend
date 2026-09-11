from rest_framework.permissions import BasePermission

from apps.accounts.models import AppRole


class HasAccessLevel(BasePermission):
    """Base class for the access_level-gated permission classes below. See
    PRD.md §3 for the four access levels and what each may do."""

    allowed_levels: tuple[str, ...] = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.access_level in self.allowed_levels)


def access_level_permission(*levels: str) -> type[HasAccessLevel]:
    """Factory for a permission class gated to specific access levels, e.g.
    `permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]`.

    RATEPAYER/RATEPAYER_PROXY must never be passed here — they only ever use
    IsRatepayerOrDelegate below, on the dedicated ratepayer-portal endpoints
    in apps.registry.api. See tests/test_rbac_closed_world.py, which fails
    the build if either level reaches a staff-facing view through this
    factory."""
    return type("_HasAccessLevel", (HasAccessLevel,), {"allowed_levels": levels})


class IsRatepayerOrDelegate(BasePermission):
    """Gate for the ratepayer self-service portal (apps.registry.api.
    RatepayerPortalViewSet) — the RATEPAYER/RATEPAYER_PROXY counterpart to
    access_level_permission, kept as its own class rather than another
    access_level_permission(...) call so the two worlds (staff endpoints vs.
    a ratepayer's own data) can never be accidentally merged by passing both
    kinds of level to the same factory call.

    view-level: caller must be a RATEPAYER or RATEPAYER_PROXY at all.
    Per-payer scoping (is this *their own* account, or one they've been
    delegated) is enforced separately by
    apps.registry.services.accessible_payer_ids, which every action in that
    viewset filters its queryset through — this class alone does not prove
    ownership of any specific payer."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.access_level in (AppRole.RATEPAYER, AppRole.RATEPAYER_PROXY)
        )
