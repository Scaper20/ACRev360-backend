from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

_HealthResponseSerializer = inline_serializer(
    "HealthResponse",
    {"status": serializers.CharField(), "service": serializers.CharField(), "time": serializers.DateTimeField()},
)


class HealthView(APIView):
    """For uptime checks, not an API status page."""

    permission_classes = [AllowAny]

    @extend_schema(responses=OpenApiResponse(_HealthResponseSerializer), tags=["health"])
    def get(self, request):
        return Response({"status": "ok", "service": "ACRev360 API", "time": timezone.now()})
