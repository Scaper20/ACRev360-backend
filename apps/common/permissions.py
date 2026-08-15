from rest_framework.permissions import BasePermission


class HasAccessLevel(BasePermission):
    """Base class for the access_level-gated permission classes below. See
    PRD.md §3 for the four access levels and what each may do."""

    allowed_levels: tuple[str, ...] = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.access_level in self.allowed_levels)


def access_level_permission(*levels: str) -> type[HasAccessLevel]:
    """Factory for a permission class gated to specific access levels, e.g.
    `permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]`."""
    return type("_HasAccessLevel", (HasAccessLevel,), {"allowed_levels": levels})
