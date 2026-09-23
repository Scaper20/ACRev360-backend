"""
Sets the request-wide RLS tenant context from the caller's JWT — see
V2_ARCHITECTURE.md §3 and apps/tenancy/context.py.

The whole request (all downstream middleware, URL resolution, DRF authentication,
the view, everything) runs inside one transaction opened here, with
`app.council_id` set from the token's `council_id` claim before any of it runs.
Decoding the token needs no DB access, so there's no bootstrap problem: the tenant
context is known before the first query of the request even happens.

Requests with no valid bearer token get `council_id = NULL` — RLS then denies every
row on every tenant-scoped table by construction (`council_id = NULL` is never true),
which is the correct default-deny for anonymous traffic. The small number of
genuinely public endpoints (bill lookup, receipt verification) re-scope themselves
explicitly per-call — see apps/tenancy/context.py's `council_context()` /
`find_across_active_councils()`.

Deliberately out of scope for this build pass: Django's session-authenticated
`/admin/` for tenant-scoped models (Bill, Payment, etc.) — the API is this product's
real surface. `/admin/` stays usable for council-agnostic bootstrapping (e.g.
Council, RevenueItemTemplate) where no tenant context is needed.
"""
from django.db import transaction
from django.http import JsonResponse
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.tenancy.context import set_council_context

#: Endpoints a must-change-password user is allowed to reach before satisfying
#: the forced change. Everything else returns 428 until they do.
_PASSWORD_CHANGE_ALLOWED_PATHS = (
    "/api/v1/auth/change-password",
    "/api/v1/auth/logout",
    "/api/v1/auth/me",
)


class CouncilContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = self._decode_token(request)

        if token is not None and token.get("must_change_password") and request.path not in _PASSWORD_CHANGE_ALLOWED_PATHS:
            return JsonResponse(
                {"error": "A password change is required before this action.", "code": "password_change_required"},
                status=428,
            )

        council_id = token.get("council_id") if token is not None else None
        with transaction.atomic():
            set_council_context(council_id)
            response = self.get_response(request)
        return response

    @staticmethod
    def _decode_token(request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        raw_token = auth_header[len("Bearer ") :].strip()
        try:
            return AccessToken(raw_token)
        except TokenError:
            return None
