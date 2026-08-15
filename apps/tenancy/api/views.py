from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.common.permissions import access_level_permission
from apps.tenancy.api.serializers import CouncilSerializer, OnboardCouncilSerializer, WardZoneSerializer
from apps.tenancy.models import WardZone
from apps.tenancy.services import onboard_council


class WardZoneViewSet(viewsets.ModelViewSet):
    serializer_class = WardZoneSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.request.method == "POST":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW)()]

    def get_queryset(self):
        return WardZone.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        serializer.save(council_id=self.request.user.council_id)


class OnboardCouncilView(APIView):
    """Platform-level bootstrap: create council -> configure -> ready for
    activate_template_item calls. Gated on Django's own is_superuser/is_staff, not
    a business access_level — creating a new tenant sits outside any existing
    council's context, see apps/tenancy/services.py."""

    permission_classes = [IsAdminUser]

    @extend_schema(request=OnboardCouncilSerializer, responses={201: CouncilSerializer}, tags=["tenancy"])
    def post(self, request):
        serializer = OnboardCouncilSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        council = onboard_council(
            council_code=data["council_code"],
            council_name=data["council_name"],
            config=data["config"],
            actor=request.user,
        )
        return Response(CouncilSerializer(council).data, status=status.HTTP_201_CREATED)
