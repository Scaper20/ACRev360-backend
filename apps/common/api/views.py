"""
Cross-council list/retrieve support for platform-tier (council=null) users.

Root bug this fixes: a platform-wide view used to do

    ids = [o.id for o in platform_wide_queryset(fn, user)]
    return Model.objects.filter(id__in=ids)

The id list is correct (platform_wide_queryset evaluates each council's
queryset *inside* that council's RLS context), but the follow-up
``filter(id__in=ids)`` queryset is lazy — DRF evaluates it later, outside any
council_context, so FORCE ROW LEVEL SECURITY (council_id = current_setting,
which is NULL there) silently yields zero rows. AuditLogViewSet was already
correct because it returns a materialized Python list directly.

PlatformWideListMixin keeps that invariant: for council=null users it
materializes rows via platform_wide_queryset, paginates the Python list, and
resolves get_object() across the user's councils. get_queryset() returns an
unevaluated .none() queryset so DRF's own default list/retrieve machinery can
never hit the RLS-null context by accident. Single-council users keep the
normal queryset path untouched.
"""
from django.http import Http404

from rest_framework.response import Response

from apps.common.platform_scope import platform_wide_queryset


class PlatformWideListMixin:
    """Mixin fixing platform-tier list/detail reads. The concrete view must
    implement ``get_platform_queryset_fn(council_id)`` returning the per-council
    queryset (search/ordering filters included) and return ``Model.objects.none()``
    from its platform-wide ``get_queryset()`` branch."""

    def get_platform_queryset_fn(self, council_id):
        raise NotImplementedError("Subclasses must define get_platform_queryset_fn(council_id).")

    def _is_platform_wide(self):
        return self.request.user.council_id is None

    def _materialize(self):
        return platform_wide_queryset(self.get_platform_queryset_fn, self.request.user)

    def list(self, request, *args, **kwargs):
        if not self._is_platform_wide():
            return super().list(request, *args, **kwargs)
        rows = self._materialize()
        page = self.paginate_queryset(rows)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(rows, many=True)
        return Response(serializer.data)

    def get_object(self):
        if not self._is_platform_wide():
            return super().get_object()
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        lookup = self.kwargs.get(lookup_url_kwarg)
        if lookup is None:
            return super().get_object()
        for row in platform_wide_queryset(
            lambda council_id: self.get_platform_queryset_fn(council_id).filter(
                **{self.lookup_field: lookup}
            ),
            self.request.user,
        ):
            self.check_object_permissions(self.request, row)
            return row
        raise Http404


class GeneratedPasswordCreateMixin:
    """Surfaces a provisioned-generated account password exactly once in the
    create response. perform_create (or a nested onboarding action) stores it
    via ``self._last_generated_password`` plus an optional
    ``self.generated_password_key`` (default "generated_password"), and the
    mixin's create() appends it to the response body."""

    generated_password_key = "generated_password"

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        generated = getattr(self, "_last_generated_password", None)
        if generated and isinstance(response.data, dict):
            response.data[self.generated_password_key] = generated
            response.data["_password_warning"] = "Shown once — share it with the account holder now, it cannot be retrieved again."
        return response