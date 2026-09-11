from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.common.permissions import access_level_permission
from apps.tenancy.api.serializers import CouncilSerializer, DepartmentSerializer, OnboardCouncilSerializer, WardZoneSerializer
from apps.tenancy.models import Department, WardZone
from apps.tenancy.services import onboard_council


class WardZoneViewSet(viewsets.ModelViewSet):
    serializer_class = WardZoneSerializer
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.request.method == "POST":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return [access_level_permission(
            AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW,
            AppRole.REVENUE_OFFICER, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR,
            AppRole.COUNCIL_IT, AppRole.CONSULTANT_STAFF, AppRole.AGENT_SUPERVISOR,
        )()]

    def get_queryset(self):
        return WardZone.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        serializer.save(council_id=self.request.user.council_id)


class DepartmentViewSet(viewsets.ModelViewSet):
    """List/create/edit council departments for grouping revenue items under —
    see CouncilRevenueItemViewSet.department for the assignment side."""

    serializer_class = DepartmentSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.request.method in ("POST", "PATCH"):
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return [access_level_permission(
            AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW, AppRole.REVENUE_OFFICER,
            AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR,
            AppRole.COUNCIL_IT, AppRole.CONSULTANT_STAFF, AppRole.AGENT_SUPERVISOR,
        )()]

    def get_queryset(self):
        return Department.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        serializer.save(council_id=self.request.user.council_id)


class OnboardCouncilView(APIView):
    """Platform-level bootstrap: create council -> configure -> ready for
    activate_template_item calls. Gated on Django's own is_superuser/is_staff OR
    a SUPER_ADMIN/PLATFORM_ADMIN business login (docs/RBAC_EXPANSION_DESIGN.md) —
    creating a new tenant sits outside any existing council's context either
    way, see apps/tenancy/services.py."""

    permission_classes = [IsAdminUser | access_level_permission(AppRole.SUPER_ADMIN, AppRole.PLATFORM_ADMIN)]

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
