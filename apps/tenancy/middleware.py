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
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.tenancy.context import set_council_context


class CouncilContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        council_id = self._resolve_council_id(request)
        with transaction.atomic():
            set_council_context(council_id)
            response = self.get_response(request)
        return response

    @staticmethod
    def _resolve_council_id(request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        raw_token = auth_header[len("Bearer ") :].strip()
        try:
            token = AccessToken(raw_token)
        except TokenError:
            return None
        return token.get("council_id")
