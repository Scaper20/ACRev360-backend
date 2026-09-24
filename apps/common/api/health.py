from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.settings import api_settings
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.net import client_ip

_HealthResponseSerializer = inline_serializer(
    "HealthResponse",
    {"status": serializers.CharField(), "service": serializers.CharField(), "time": serializers.DateTimeField()},
)


class HealthView(APIView):
    """For uptime checks, not an API status page."""

    permission_classes = [AllowAny]
    # Never throttled: Render's health check and any uptime pinger hit this from
    # a handful of shared addresses, and a 429 here reads as "service down" and
    # gets the instance restarted.
    throttle_classes = []

    @extend_schema(responses=OpenApiResponse(_HealthResponseSerializer), tags=["health"])
    def get(self, request):
        return Response({"status": "ok", "service": "ACRev360 API", "time": timezone.now()})


class ClientIPView(APIView):
    """What the server sees of the caller's own connection — the raw proxy
    headers and the address the anonymous throttles will key on. It exists so the
    production ``NUM_PROXIES`` value (docs/DEPLOYMENT.md §8) is read off a real
    request instead of guessed: log in, call this once, and count the entries in
    ``x_forwarded_for`` from the right. Signed-in callers only, and it echoes
    nothing but that caller's own request headers."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=OpenApiResponse(inline_serializer("ClientIPResponse", {
            "remote_addr": serializers.CharField(allow_null=True),
            "x_forwarded_for": serializers.CharField(allow_null=True),
            "cf_connecting_ip": serializers.CharField(allow_null=True),
            "true_client_ip": serializers.CharField(allow_null=True),
            "num_proxies": serializers.IntegerField(allow_null=True),
            "resolved_ip": serializers.CharField(allow_null=True),
        })),
        tags=["health"],
    )
    def get(self, request):
        meta = request.META
        return Response({
            "remote_addr": meta.get("REMOTE_ADDR"),
            "x_forwarded_for": meta.get("HTTP_X_FORWARDED_FOR"),
            "cf_connecting_ip": meta.get("HTTP_CF_CONNECTING_IP"),
            "true_client_ip": meta.get("HTTP_TRUE_CLIENT_IP"),
            "num_proxies": api_settings.NUM_PROXIES,
            "resolved_ip": client_ip(request),
        })
