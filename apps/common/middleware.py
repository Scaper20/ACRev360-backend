"""
Cheap request-level guards that sit in front of everything else.
"""
from urllib.parse import unquote

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from apps.common.net import client_ip


class RejectNulBytesMiddleware:
    """A NUL byte in a query string (``?q=%00``) sails through Django and DRF and
    is only rejected by PostgreSQL ("A string literal cannot contain NUL
    characters"), which surfaces as an unhandled 500 on every search endpoint.
    JSON request bodies are already covered — DRF's CharField refuses NUL — so
    the query string is the one gap. Turning it into a 400 here covers every
    present and future ``?q=``/filter parameter in one place instead of
    sanitising each view's copy of it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        query = request.META.get("QUERY_STRING", "")
        if "%00" in query.lower() or "\x00" in unquote(query):
            return JsonResponse({"error": "Query parameters may not contain NUL characters."}, status=400)
        return self.get_response(request)


class AdminLoginThrottleMiddleware:
    """Django admin's own login form is a second, unthrottled password-guessing
    surface (the JWT login has per-email limits; this doesn't go through it).
    Counts POSTs to the admin login path per client address in a fixed window
    and answers 429 once the budget is spent. Budget/window are env-tunable
    (ADMIN_LOGIN_ATTEMPTS / ADMIN_LOGIN_WINDOW_SECONDS); dev/test keep it
    effectively off."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path.rstrip("/").endswith("/login") and request.path.startswith(
            f"/{settings.ADMIN_URL}"
        ):
            key = f"admin-login:{client_ip(request) or request.META.get('REMOTE_ADDR', '?')}"
            window = settings.ADMIN_LOGIN_WINDOW_SECONDS
            cache.add(key, 0, timeout=window)
            try:
                attempts = cache.incr(key)
            except ValueError:  # key expired between add() and incr()
                cache.set(key, 1, timeout=window)
                attempts = 1
            if attempts > settings.ADMIN_LOGIN_ATTEMPTS:
                return JsonResponse({"error": "Too many login attempts. Try again later."}, status=429)
        return self.get_response(request)
