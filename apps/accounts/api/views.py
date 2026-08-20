from django.db import models
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.api.serializers import (
    AgentPortfolioSerializer,
    ConsultantPortfolioSerializer,
    FieldAgentSerializer,
    LogoutRequestSerializer,
    MeSerializer,
    StakeholderSerializer,
    SubConsultantSerializer,
    SubConsultantStatusSerializer,
)
from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.accounts.tokens import AppTokenObtainPairSerializer
from apps.audit.services import audit
from apps.common.permissions import access_level_permission
from apps.payments.api.serializers import PaymentSerializer
from apps.revenue.models import AgentPortfolio, ConsultantPortfolio


class AgentActivityResponseSerializer(serializers.Serializer):
    today_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    recent_payments = PaymentSerializer(many=True)


class TokenPairResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


@extend_schema_view(
    post=extend_schema(
        responses=TokenPairResponseSerializer,
        description="access/refresh JWT pair. The access token carries council_id/access_level/"
        "consultant_id claims used to scope every subsequent request — see apps/tenancy/middleware.py.",
        tags=["auth"],
    )
)
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


@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="Search by consultant name or contract reference")])
)
class SubConsultantViewSet(viewsets.ModelViewSet):
    serializer_class = SubConsultantSerializer
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"
    # Governs list/retrieve/create (and end_portfolio, which declares no
    # override of its own). Consultant names/commission/status are exactly
    # what a stakeholder account must not see (see StakeholderViewSet's
    # docstring), so this is COUNCIL_ADMIN-only — a consultant's own identity
    # is exposed instead via /auth/me (MeSerializer.consultant_name etc.).
    # status_change and portfolio below declare their own, wider
    # permission_classes on the @action itself — a custom get_permissions()
    # here would silently shadow those per-action overrides (DRF applies
    # them by setting self.permission_classes before dispatch, which only
    # the default get_permissions() reads), so this deliberately stays a
    # plain class attribute rather than a method.
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]

    def get_queryset(self):
        qs = SubConsultant.objects.filter(council_id=self.request.user.council_id).order_by("consultant_name")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(consultant_name__icontains=q) | models.Q(contract_ref__icontains=q))
        return qs

    def perform_create(self, serializer):
        data = serializer.validated_data
        manager_username = data.pop("manager_username", None)
        manager_password = data.pop("manager_password", None)
        manager_full_name = data.pop("manager_full_name", None)

        instance = serializer.save(council_id=self.request.user.council_id, status=SubConsultant.PENDING)
        audit(
            council_id=instance.council_id, actor=self.request.user, action="CONSULTANT_ONBOARDED",
            entity_type="SUB_CONSULTANT", entity_id=instance.id,
            detail={"consultant_name": instance.consultant_name, "manager_login_created": bool(manager_username)},
        )

        if manager_username:
            consultant_role, _ = AppRole.objects.get_or_create(name="CONSULTANT_MANAGER", defaults={"access_level": AppRole.CONSULTANT})
            AppUser.objects.create_user(
                username=manager_username, password=manager_password or "acrev360-2026", full_name=manager_full_name,
                council_id=instance.council_id, role=consultant_role, consultant=instance,
            )
            audit(
                council_id=instance.council_id, actor=self.request.user, action="CONSULTANT_MANAGER_ONBOARDED",
                entity_type="SUB_CONSULTANT", entity_id=instance.id, detail={"username": manager_username},
            )

    @extend_schema(request=SubConsultantStatusSerializer, responses=SubConsultantSerializer)
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

    @extend_schema(methods=["GET"], responses=ConsultantPortfolioSerializer(many=True))
    @extend_schema(methods=["POST"], request=ConsultantPortfolioSerializer, responses=ConsultantPortfolioSerializer)
    @action(
        detail=True, methods=["get", "post"], pagination_class=None,
        # Wider than get_permissions()'s COUNCIL_ADMIN-only default — a
        # consultant manager needs to see their own portfolio for their
        # dashboard. The method body below still 403s a CONSULTANT trying to
        # read another firm's portfolio, or POST at all (assignment stays
        # COUNCIL_ADMIN-only).
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)],
    )
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
        already_assigned = ConsultantPortfolio.objects.filter(
            consultant=consultant, council_revenue_item=serializer.validated_data["council_revenue_item"],
            ward=serializer.validated_data.get("ward"), effective_to__isnull=True,
        ).exists()
        if already_assigned:
            return Response({"error": "This item is already assigned to this consultant"}, status=status.HTTP_400_BAD_REQUEST)
        entry = serializer.save(council_id=consultant.council_id, consultant=consultant)
        audit(
            council_id=consultant.council_id, actor=request.user, action="PORTFOLIO_ASSIGNED", entity_type="SUB_CONSULTANT",
            entity_id=consultant.id, detail={"council_revenue_item_id": entry.council_revenue_item_id},
        )
        return Response(ConsultantPortfolioSerializer(entry).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("portfolio_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=None,
        responses=ConsultantPortfolioSerializer,
    )
    @action(detail=True, methods=["post"], url_path=r"portfolio/(?P<portfolio_id>[0-9]+)/end")
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


@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("q", OpenApiTypes.STR, description="Search by agent code or agent name")])
)
class FieldAgentViewSet(viewsets.ModelViewSet):
    serializer_class = FieldAgentSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)]
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        user = self.request.user
        qs = FieldAgent.objects.filter(council_id=user.council_id)
        if user.access_level == AppRole.CONSULTANT:
            qs = qs.filter(user__consultant_id=user.consultant_id)
        elif user.access_level == AppRole.AGENT:
            # Only relevant to the `activity` action below (list/retrieve/
            # create stay COUNCIL_ADMIN/CONSULTANT-only via the class-level
            # permission_classes) — scoping here too means an agent
            # reaching for another agent's id 404s at get_object(), rather
            # than relying solely on activity()'s own ownership check.
            qs = qs.filter(user_id=user.id)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(agent_code__icontains=q) | models.Q(user__full_name__icontains=q))
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

    @extend_schema(methods=["GET"], responses=AgentPortfolioSerializer(many=True))
    @extend_schema(methods=["POST"], request=AgentPortfolioSerializer, responses=AgentPortfolioSerializer)
    @action(detail=True, methods=["get", "post"], pagination_class=None)
    def portfolio(self, request, pk=None):
        """Which revenue items this specific agent may handle — an optional
        further narrowing of their own consultant's ConsultantPortfolio (see
        AgentPortfolio's docstring). get_queryset() already scopes a
        CONSULTANT caller to their own agents, so no extra ownership check is
        needed here beyond get_object() itself — a consultant reaching this
        for another firm's agent 404s before this method body ever runs."""
        agent = self.get_object()

        if request.method == "GET":
            entries = agent.portfolio.filter(effective_to__isnull=True)
            return Response(AgentPortfolioSerializer(entries, many=True).data)

        serializer = AgentPortfolioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.validated_data["council_revenue_item"]
        consultant_id = agent.user.consultant_id
        if consultant_id is None:
            return Response(
                {"error": "This agent is council-direct — item assignment only applies to a consultant's own agents"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # "out of their given ones" — an agent can only be handed a subset of
        # what their own consultant is actually allowed to work, not anything
        # from the council's wider chart of revenue.
        in_consultant_portfolio = ConsultantPortfolio.objects.filter(
            consultant_id=consultant_id, council_revenue_item=item, effective_to__isnull=True,
        ).exists()
        if not in_consultant_portfolio:
            return Response(
                {"error": "This item isn't in the agent's own consultant's portfolio"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        already_assigned = AgentPortfolio.objects.filter(
            agent=agent, council_revenue_item=item, ward=serializer.validated_data.get("ward"), effective_to__isnull=True,
        ).exists()
        if already_assigned:
            return Response({"error": "This item is already assigned to this agent"}, status=status.HTTP_400_BAD_REQUEST)
        entry = serializer.save(council_id=agent.council_id, agent=agent)
        audit(
            council_id=agent.council_id, actor=request.user, action="AGENT_PORTFOLIO_ASSIGNED", entity_type="FIELD_AGENT",
            entity_id=agent.id, detail={"council_revenue_item_id": entry.council_revenue_item_id},
        )
        return Response(AgentPortfolioSerializer(entry).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("portfolio_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=None,
        responses=AgentPortfolioSerializer,
    )
    @action(detail=True, methods=["post"], url_path=r"portfolio/(?P<portfolio_id>[0-9]+)/end")
    def end_portfolio(self, request, pk=None, portfolio_id=None):
        agent = self.get_object()
        entry = AgentPortfolio.objects.get(pk=portfolio_id, agent=agent)
        entry.effective_to = timezone.localdate()
        entry.save(update_fields=["effective_to"])
        audit(
            council_id=agent.council_id, actor=request.user, action="AGENT_PORTFOLIO_REVOKED", entity_type="FIELD_AGENT",
            entity_id=agent.id, detail={"portfolio_id": entry.id},
        )
        return Response(AgentPortfolioSerializer(entry).data)

    @extend_schema(responses=AgentActivityResponseSerializer)
    @action(
        detail=True, methods=["get"],
        # Wider than get_permissions()'s COUNCIL_ADMIN/CONSULTANT default —
        # this is also the mobile agent app's own "today's tally" tile, so
        # an agent needs to read their own activity. get_queryset() already
        # scopes an AGENT caller to their own FieldAgent row (another
        # agent's id 404s before this body runs); the check below is
        # belt-and-suspenders against a future get_queryset() change.
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)],
    )
    def activity(self, request, pk=None):
        """Recent payments posted by this agent — backs both the admin/
        consultant detail view and the mobile app's status view. The
        fieldops app (worklist, offline sync) builds on top of this rather
        than duplicating it — see fieldops.services.get_worklist."""
        from apps.payments.api.serializers import PaymentSerializer
        from apps.payments.models import Payment

        agent = self.get_object()
        if request.user.access_level == AppRole.AGENT and agent.user_id != request.user.id:
            return Response({"error": "Not your activity"}, status=status.HTTP_403_FORBIDDEN)
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


class StakeholderViewSet(viewsets.ModelViewSet):
    """Read-only oversight accounts (GLOBAL_VIEW access level) — council/FCT
    stakeholders who need a performance pulse but must never see individual
    payer or sub-consultant identities. That boundary is enforced elsewhere
    (GLOBAL_VIEW is deliberately absent from PayerViewSet, BillViewSet,
    PaymentViewSet, ReceiptViewSet and SubConsultantViewSet's permissions,
    and DashboardGlobalView anonymizes its per-consultant breakdown for this
    role) — this viewset only manages the accounts themselves, and that
    management is COUNCIL_ADMIN-only both ways."""

    serializer_class = StakeholderSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        return AppUser.objects.filter(
            council_id=self.request.user.council_id, role__access_level=AppRole.GLOBAL_VIEW
        ).order_by("full_name")

    def perform_create(self, serializer):
        user = self.request.user
        data = serializer.validated_data
        stakeholder_role, _ = AppRole.objects.get_or_create(name="STAKEHOLDER", defaults={"access_level": AppRole.GLOBAL_VIEW})
        instance = AppUser.objects.create_user(
            username=data.pop("username"),
            password=data.pop("password", "acrev360-2026"),
            full_name=data.pop("full_name"),
            phone=data.pop("phone", ""),
            council_id=user.council_id,
            role=stakeholder_role,
        )
        serializer.instance = instance
        audit(
            council_id=user.council_id, actor=user, action="STAKEHOLDER_ONBOARDED",
            entity_type="APP_USER", entity_id=instance.id, detail={"username": instance.username},
        )
