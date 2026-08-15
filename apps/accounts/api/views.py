from django.db import models
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.api.serializers import (
    ConsultantPortfolioSerializer,
    FieldAgentSerializer,
    LogoutRequestSerializer,
    MeSerializer,
    SubConsultantSerializer,
    SubConsultantStatusSerializer,
)
from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.accounts.tokens import AppTokenObtainPairSerializer
from apps.audit.services import audit
from apps.common.permissions import access_level_permission
from apps.revenue.models import ConsultantPortfolio


class LoginView(TokenObtainPairView):
    permission_classes = [AllowAny]
    serializer_class = AppTokenObtainPairSerializer


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=LogoutRequestSerializer, responses={204: None}, tags=["auth"])
    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response({"error": "refresh token required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(generics.RetrieveAPIView):
    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class SubConsultantViewSet(viewsets.ModelViewSet):
    serializer_class = SubConsultantSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.request.method == "POST":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.GLOBAL_VIEW)()]

    def get_queryset(self):
        return SubConsultant.objects.filter(council_id=self.request.user.council_id).order_by("consultant_name")

    def perform_create(self, serializer):
        instance = serializer.save(council_id=self.request.user.council_id, status=SubConsultant.PENDING)
        audit(
            council_id=instance.council_id, actor=self.request.user, action="CONSULTANT_ONBOARDED",
            entity_type="SUB_CONSULTANT", entity_id=instance.id, detail={"consultant_name": instance.consultant_name},
        )

    @action(detail=True, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def status_change(self, request, pk=None):
        consultant = self.get_object()
        serializer = SubConsultantStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_status = consultant.status
        consultant.status = serializer.validated_data["status"]
        consultant.save(update_fields=["status", "updated_at"])
        audit(
            council_id=consultant.council_id, actor=request.user, action="CONSULTANT_STATUS_CHANGED",
            entity_type="SUB_CONSULTANT", entity_id=consultant.id, detail={"old_status": old_status, "new_status": consultant.status},
        )
        return Response(SubConsultantSerializer(consultant).data)

    @action(detail=True, methods=["get", "post"])
    def portfolio(self, request, pk=None):
        consultant = self.get_object()
        if request.user.access_level == AppRole.CONSULTANT and request.user.consultant_id != consultant.id:
            return Response({"error": "Not your portfolio"}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "GET":
            entries = consultant.portfolio.filter(effective_to__isnull=True)
            return Response(ConsultantPortfolioSerializer(entries, many=True).data)

        if request.user.access_level != AppRole.COUNCIL_ADMIN:
            return Response({"error": "Only council admin may assign a portfolio"}, status=status.HTTP_403_FORBIDDEN)
        serializer = ConsultantPortfolioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(council_id=consultant.council_id, consultant=consultant)
        audit(
            council_id=consultant.council_id, actor=request.user, action="PORTFOLIO_ASSIGNED", entity_type="SUB_CONSULTANT",
            entity_id=consultant.id, detail={"council_revenue_item_id": entry.council_revenue_item_id},
        )
        return Response(ConsultantPortfolioSerializer(entry).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="portfolio/(?P<portfolio_id>[^/.]+)/end")
    def end_portfolio(self, request, pk=None, portfolio_id=None):
        consultant = self.get_object()
        entry = ConsultantPortfolio.objects.get(pk=portfolio_id, consultant=consultant)
        entry.effective_to = timezone.localdate()
        entry.save(update_fields=["effective_to"])
        audit(
            council_id=consultant.council_id, actor=request.user, action="PORTFOLIO_REVOKED", entity_type="SUB_CONSULTANT",
            entity_id=consultant.id, detail={"portfolio_id": entry.id},
        )
        return Response(ConsultantPortfolioSerializer(entry).data)


class FieldAgentViewSet(viewsets.ModelViewSet):
    serializer_class = FieldAgentSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = FieldAgent.objects.filter(council_id=user.council_id)
        if user.access_level == AppRole.CONSULTANT:
            qs = qs.filter(user__consultant_id=user.consultant_id)
        return qs.order_by("agent_code")

    def perform_create(self, serializer):
        user = self.request.user
        data = serializer.validated_data
        agent_role, _ = AppRole.objects.get_or_create(name="FIELD_AGENT", defaults={"access_level": AppRole.AGENT})
        consultant_id = user.consultant_id if user.access_level == AppRole.CONSULTANT else self.request.data.get("consultant_id")

        app_user = AppUser.objects.create_user(
            username=data.pop("username"),
            password=data.pop("password", "acrev360-2026"),
            full_name=data.pop("full_name"),
            phone=data.pop("phone", ""),
            council_id=user.council_id,
            role=agent_role,
            consultant_id=consultant_id,
        )
        next_seq = FieldAgent.objects.filter(council_id=user.council_id).count() + 1
        agent = serializer.save(
            council_id=user.council_id,
            user=app_user,
            agent_code=f"AGT-{next_seq:05d}",
        )
        audit(
            council_id=user.council_id, actor=user, action="AGENT_ONBOARDED", entity_type="FIELD_AGENT",
            entity_id=agent.id, detail={"agent_code": agent.agent_code},
        )

    @action(detail=True, methods=["get"])
    def activity(self, request, pk=None):
        """Recent payments posted by this agent. The full daily-returns rollup
        (`agent_daily_return`, worklist, offline sync) is fieldops — deferred to
        V2_ARCHITECTURE.md §11 phase 4, out of this build pass."""
        from apps.payments.api.serializers import PaymentSerializer
        from apps.payments.models import Payment

        agent = self.get_object()
        recent_payments = Payment.objects.filter(posted_by=agent.user).order_by("-created_at")[:20]
        today_total = (
            Payment.objects.filter(posted_by=agent.user, created_at__date=timezone.localdate())
            .aggregate(total=models.Sum("amount"))["total"]
            or 0
        )
        return Response({
            "today_total": today_total,
            "recent_payments": PaymentSerializer(recent_payments, many=True).data,
        })
