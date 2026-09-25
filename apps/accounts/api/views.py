from django.db import IntegrityError, models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.accounts.api.serializers import (
    AgentPortfolioSerializer,
    AssignPayerSerializer,
    ChangePasswordSerializer,
    ConsultantPortfolioSerializer,
    FieldAgentSerializer,
    FieldAgentStatusSerializer,
    LogoutRequestSerializer,
    MeSerializer,
    RevenueOfficerSerializer,
    StakeholderSerializer,
    SubConsultantContractDatesSerializer,
    SubConsultantSerializer,
    SubConsultantStatusSerializer,
    UpdateProfileSerializer,
)
from apps.accounts.models import AppRole, AppUser, FieldAgent, SubConsultant
from apps.accounts.security import provision_password, revoke_all_sessions
from apps.accounts.throttles import LoginEmailBurstThrottle, LoginEmailSustainedThrottle
from apps.accounts.tokens import AppTokenObtainPairSerializer
from apps.audit.services import audit, audit_user_event
from apps.billing.models import Bill
from apps.billing.services import BillingError, issue_bill
from apps.common.api.views import GeneratedPasswordCreateMixin, PlatformWideListMixin
from apps.common.filtering import StableOrderingFilter
from apps.common.net import client_ip
from apps.common.permissions import access_level_permission
from apps.payments.api.serializers import PaymentSerializer
from apps.registry.api.serializers import PayerSerializer
from apps.registry.models import Payer
from apps.registry.services import create_payer, split_full_name
from apps.revenue.models import AgentPortfolio, ConsultantPortfolio, CouncilRevenueItem, RateBand
from apps.tenancy.models import WardZone

# The revenue item + band that stand in for "consultant firm registration" —
# see SubConsultantViewSet.perform_create. Confirmed against the seeded
# Contractors item's existing flat-rate "Consultancy" band rather than adding
# a new item.
CONSULTANT_REGISTRATION_ITEM_CODE = "30010048"
CONSULTANT_REGISTRATION_BAND_LABEL = "Consultancy"


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
    # Public credential-checking endpoint — brute-force throttled, see
    # DEFAULT_THROTTLE_RATES in config/settings. The rest of the API needs a
    # valid JWT, so this is the only thing that needs a budget. The two
    # per-email throttles are the ones that actually hold: the IP-scoped one
    # keys on X-Forwarded-For, which a client can rotate (see
    # apps/accounts/throttles.py for the confirmed bypass).
    throttle_classes = [ScopedRateThrottle, LoginEmailBurstThrottle, LoginEmailSustainedThrottle]
    throttle_scope = "login"


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


@extend_schema_view(
    # get_serializer_class() (used for the *request* body) leads
    # drf-spectacular to also document the 200 response as UpdateProfile —
    # this response is actually the full MeSerializer shape, per the comment
    # on update() below. Neither a plain @extend_schema directly on the
    # update() method override, nor this class-level extend_schema_view
    # form, actually corrects the generated response type (confirmed via two
    # separate rebuild+regenerate cycles each) — drf-spectacular appears to
    # resolve the response schema for a GenericAPIView method override from
    # get_serializer_class() itself, ahead of either override mechanism.
    # Left in place as accurate intent/documentation; the actual fix is a
    # manual type override on the frontend (packages/api/src/overrides.ts),
    # same fallback already used there for POST /payments.
    update=extend_schema(request=UpdateProfileSerializer, responses=MeSerializer),
)
class MeView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        return UpdateProfileSerializer if self.request.method == "PATCH" else MeSerializer

    def update(self, request, *args, **kwargs):
        # UpdateProfileSerializer only carries full_name/email/phone — after
        # saving, respond with the full MeSerializer shape instead, so the
        # frontend doesn't need a second round trip just to get
        # consultant_name/agent_code/etc. back into its own state.
        instance = self.get_object()
        previous_email = instance.email
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if instance.email != previous_email:
            audit_user_event(
                user=instance, action="EMAIL_CHANGED", detail={"old_email": previous_email, "new_email": instance.email},
                actor_ip=client_ip(request),
            )
        return Response(MeSerializer(instance).data)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=ChangePasswordSerializer, responses={204: None}, tags=["auth"])
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            return Response({"error": "Current password is incorrect"}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(serializer.validated_data["new_password"])
        update_fields = ["password"]
        if user.must_change_password:
            # The forced-change contract: this is the moment the system-
            # generated password becomes the person's own, so lift the
            # must_change flag (and the 428 gate it drives) right here.
            user.must_change_password = False
            update_fields.append("must_change_password")
        user.save(update_fields=update_fields)
        # Ends every other session too — including any an attacker opened
        # with the old password. This device signs back in on its next token
        # refresh (access tokens live 30 minutes).
        revoked = revoke_all_sessions(user)
        audit_user_event(
            user=user, action="PASSWORD_CHANGED", detail={"sessions_revoked": revoked}, actor_ip=client_ip(request),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by consultant name or contract reference"),
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by status — PENDING, ACTIVE, SUSPENDED, EXITED"),
        ]
    )
)
class SubConsultantViewSet(PlatformWideListMixin, GeneratedPasswordCreateMixin, viewsets.ModelViewSet):
    serializer_class = SubConsultantSerializer
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"
    # Council-tier only — PlatformWideListMixin's own docstring says its
    # get_platform_queryset_fn must do its own filtering/ordering, and its
    # list() only runs DRF's filter_backends machinery (this) on the
    # non-platform-wide branch (super().list()). The platform-tier path
    # below applies the same status filter manually but deliberately does
    # NOT expose ordering: platform_wide_queryset (apps/common/platform_scope.py)
    # concatenates each council's own separately-ordered results in
    # council-iteration order, not one globally re-sorted list — sorting
    # each council's block internally while leaving the blocks themselves
    # unordered relative to each other would look sorted but not actually
    # be, which is worse than visibly not supporting it.
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["consultant_name", "commission_rate", "contract_start_date", "contract_end_date", "status"]
    # Governs create (and end_portfolio, which declares no override of its
    # own) — COUNCIL_ADMIN-only, since onboarding/firm-level commercial terms
    # stay a council admin decision (docs/RBAC_EXPANSION_DESIGN.md: this is
    # explicitly NOT part of COUNCIL_IGR_HEAD's scope). list/retrieve are
    # widened in get_permissions() below to COMPLIANCE_VIEW/EXTERNAL_AUDITOR
    # (platform tier — "contracts" is literally what those two read) and the
    # council-tier read additions, including COUNCIL_IT — it can create a
    # revenue-officer login via the revenue_officers action below (its own
    # narrower permission_classes), which needs it to be able to list/
    # retrieve consultants to reach that action in the first place; missing
    # here originally, confirmed live against production by the frontend
    # team (2026-09-11 CHANGELOG entry) exactly the same way as
    # FieldAgentViewSet's identical gap. status_change/contract_dates/
    # revenue_officers/portfolio below declare their own permission_classes
    # on the @action itself, which DRF applies to self.permission_classes
    # before get_permissions() runs — get_permissions()'s super() fallback
    # for any action not branched here reads that already-reassigned value,
    # so this doesn't shadow those overrides.
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN)]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [access_level_permission(
                AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR,
                AppRole.COUNCIL_IT, AppRole.COMPLIANCE_VIEW, AppRole.EXTERNAL_AUDITOR, AppRole.SUPER_ADMIN,
                AppRole.PLATFORM_ADMIN,
            )()]
        return super().get_permissions()

    @staticmethod
    def _with_serializer_relations(qs):
        # SubConsultantSerializer reads registration_payer.payer_ref and
        # has_login per row — one join + one EXISTS subquery in the main
        # query instead of two extra queries per consultant. For the
        # platform-tier path this is also a correctness matter, not just
        # speed: rows are materialised inside each council's RLS context
        # (platform_wide_queryset), and anything left to load lazily at
        # serialisation time runs after that context has closed, where RLS
        # hides it — registration_payer_ref came back null under an
        # RLS-enforced role.
        return qs.select_related("registration_payer").annotate(
            _has_login=models.Exists(AppUser.objects.filter(consultant_id=models.OuterRef("pk")))
        )

    def _apply_common_filters(self, qs):
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(consultant_name__icontains=q) | models.Q(contract_ref__icontains=q))
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def get_platform_queryset_fn(self, council_id):
        qs = SubConsultant.objects.filter(council_id=council_id).order_by("consultant_name", "id")
        qs = self._apply_common_filters(qs)
        return self._with_serializer_relations(qs)

    def get_queryset(self):
        user = self.request.user
        if user.council_id is None:
            # Platform tier: never let a lazy queryset evaluate outside a
            # council RLS context — PlatformWideListMixin.list/get_object
            # materialize via get_platform_queryset_fn per council instead.
            return SubConsultant.objects.none()
        qs = SubConsultant.objects.filter(council_id=user.council_id).order_by("consultant_name", "id")
        qs = self._apply_common_filters(qs)
        return self._with_serializer_relations(qs)

    @transaction.atomic
    def perform_create(self, serializer):
        data = serializer.validated_data
        manager_username = data.pop("manager_username", None)
        manager_password = data.pop("manager_password", None)
        manager_full_name = data.pop("manager_full_name", None)
        registration_ward_id = data.pop("registration_ward_id")

        # Pre-check rather than letting SubConsultant's uniq_contract_ref_per_council
        # constraint raise: an uncaught IntegrityError isn't a DRF APIException, so
        # it skips DRF's exception handler entirely and falls through to Django's
        # generic 500 page — no detail to the client, and (with DEBUG off, as in
        # this project's Docker setup) nothing in the server logs either. Found live
        # onboarding a real consultant whose contract_ref collided with an existing
        # one; same pattern as the already_assigned portfolio checks below.
        if SubConsultant.objects.filter(council_id=self.request.user.council_id, contract_ref=data["contract_ref"]).exists():
            raise serializers.ValidationError({"contract_ref": "This contract reference is already in use."})

        ward = WardZone.objects.filter(id=registration_ward_id, council_id=self.request.user.council_id).first()
        if ward is None:
            raise serializers.ValidationError({"registration_ward_id": "Not a valid ward for this council."})

        # Resolved before creating anything — a misconfigured council (no
        # Contractors item/Consultancy band seeded) should fail the whole
        # onboarding up front, not leave a consultant+payer created with no
        # registration bill to show for it.
        try:
            registration_item = CouncilRevenueItem.objects.get(
                council_id=self.request.user.council_id, harmonised_code=CONSULTANT_REGISTRATION_ITEM_CODE, is_active=True,
            )
            registration_band = registration_item.active_bands.get(label=CONSULTANT_REGISTRATION_BAND_LABEL)
        except (CouncilRevenueItem.DoesNotExist, RateBand.DoesNotExist):
            raise serializers.ValidationError({
                "contract_ref": (
                    f"This council has no '{CONSULTANT_REGISTRATION_ITEM_CODE} — {CONSULTANT_REGISTRATION_BAND_LABEL}' "
                    "revenue item configured — cannot bill consultant registration."
                ),
            })

        instance = serializer.save(council_id=self.request.user.council_id, status=SubConsultant.PENDING)
        audit(
            council_id=instance.council_id, actor=self.request.user, action="CONSULTANT_ONBOARDED",
            entity_type="SUB_CONSULTANT", entity_id=instance.id,
            detail={"consultant_name": instance.consultant_name, "manager_login_created": bool(manager_username)},
        )

        if manager_username:
            consultant_role, _ = AppRole.objects.get_or_create(name="CONSULTANT_MANAGER", defaults={"access_level": AppRole.CONSULTANT})
            manager_password, must_change = provision_password(manager_password)
            AppUser.objects.create_user(
                username=manager_username, password=manager_password, full_name=manager_full_name,
                council_id=instance.council_id, role=consultant_role, consultant=instance,
                must_change_password=must_change,
            )
            if must_change:
                self.generated_password_key = "generated_manager_password"
                self._last_generated_password = manager_password
            audit(
                council_id=instance.council_id, actor=self.request.user, action="CONSULTANT_MANAGER_ONBOARDED",
                entity_type="SUB_CONSULTANT", entity_id=instance.id, detail={"username": manager_username},
            )

        # The firm as a payer, billed for its own registration — see item 7 of
        # the frontend's backend requirements doc. enumerated_by defaults to
        # the onboarding admin, same as any other admin-enumerated payer.
        first_name, middle_name, last_name = split_full_name(instance.consultant_name)
        payer, _ = create_payer(
            council_id=instance.council_id, actor=self.request.user,
            payer_type=Payer.BUSINESS, first_name=first_name, middle_name=middle_name, last_name=last_name, ward=ward,
        )
        instance.registration_payer = payer
        instance.save(update_fields=["registration_payer"])
        try:
            bill = issue_bill(
                council_id=instance.council_id, payer=payer,
                lines=[{"council_revenue_item": registration_item, "rate_band": registration_band}],
                actor=self.request.user,
            )
        except BillingError as exc:
            raise serializers.ValidationError({"contract_ref": str(exc)})
        audit(
            council_id=instance.council_id, actor=self.request.user, action="CONSULTANT_REGISTRATION_BILLED",
            entity_type="SUB_CONSULTANT", entity_id=instance.id,
            detail={"payer_ref": payer.payer_ref, "bill_ref": bill.bill_ref, "total_amount": str(bill.total_amount)},
        )

    @extend_schema(request=SubConsultantStatusSerializer, responses=SubConsultantSerializer)
    @action(detail=True, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def status_change(self, request, pk=None):
        consultant = self.get_object()
        serializer = SubConsultantStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        # Hard rule: a consultant can't go live while their own registration
        # bill is still outstanding — see item 7 of the frontend's backend
        # requirements doc. Consultants onboarded before registration_payer
        # existed have none set, so this never retroactively blocks them.
        if new_status == SubConsultant.ACTIVE and consultant.registration_payer_id:
            has_balance = Bill.objects.filter(
                payer_id=consultant.registration_payer_id, status__in=[Bill.ISSUED, Bill.PART_PAID, Bill.OVERDUE],
            ).exists()
            if has_balance:
                return Response(
                    {"error": "This consultant's registration bill still has a balance — it must be paid before activation."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        old_status = consultant.status
        consultant.status = new_status
        consultant.save(update_fields=["status", "updated_at"])
        audit(
            council_id=consultant.council_id, actor=request.user, action="CONSULTANT_STATUS_CHANGED",
            entity_type="SUB_CONSULTANT", entity_id=consultant.id, detail={"old_status": old_status, "new_status": consultant.status},
        )
        return Response(SubConsultantSerializer(consultant).data)

    @extend_schema(request=SubConsultantContractDatesSerializer, responses=SubConsultantSerializer)
    @action(detail=True, methods=["post"], permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def contract_dates(self, request, pk=None):
        consultant = self.get_object()
        serializer = SubConsultantContractDatesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        start = data["contract_start_date"] if "contract_start_date" in data else consultant.contract_start_date
        end = data["contract_end_date"] if "contract_end_date" in data else consultant.contract_end_date
        if start and end and start > end:
            raise serializers.ValidationError({"contract_end_date": "Must be on or after contract_start_date."})

        consultant.contract_start_date = start
        consultant.contract_end_date = end
        consultant.save(update_fields=["contract_start_date", "contract_end_date", "updated_at"])
        audit(
            council_id=consultant.council_id, actor=request.user, action="CONSULTANT_CONTRACT_DATES_CHANGED",
            entity_type="SUB_CONSULTANT", entity_id=consultant.id,
            detail={"contract_start_date": str(start) if start else None, "contract_end_date": str(end) if end else None},
        )
        return Response(SubConsultantSerializer(consultant).data)

    @extend_schema(methods=["GET"], responses=RevenueOfficerSerializer(many=True))
    @extend_schema(methods=["POST"], request=RevenueOfficerSerializer, responses=RevenueOfficerSerializer)
    @action(
        detail=True, methods=["get", "post"], url_path="revenue-officers",
        # COUNCIL_IT included — onboarding a login is exactly its job per
        # docs/RBAC_EXPANSION_DESIGN.md, and this action grants no financial
        # capability (REVENUE_OFFICER's own permissions are read-only
        # everywhere else it appears).
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IT)],
    )
    def revenue_officers(self, request, pk=None):
        """Onboards (or lists) REVENUE_OFFICER logins scoped to this one
        consultant — read-only accounts that get the exact same portfolio
        visibility as the consultant's own manager (see common.scoping.
        portfolio_filter), enforced by staying off every mutation endpoint's
        permission_classes rather than by anything checked here."""
        consultant = self.get_object()

        if request.method == "GET":
            officers = AppUser.objects.filter(consultant=consultant, role__access_level=AppRole.REVENUE_OFFICER).order_by("full_name")
            return Response(RevenueOfficerSerializer(officers, many=True).data)

        serializer = RevenueOfficerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        officer_role, _ = AppRole.objects.get_or_create(name="REVENUE_OFFICER", defaults={"access_level": AppRole.REVENUE_OFFICER})
        password, must_change = provision_password(data.pop("password", None))
        instance = AppUser.objects.create_user(
            username=data.pop("username"),
            password=password,
            full_name=data.pop("full_name"),
            phone=data.pop("phone", ""),
            council_id=consultant.council_id, role=officer_role, consultant=consultant,
            must_change_password=must_change,
        )
        audit(
            council_id=consultant.council_id, actor=request.user, action="REVENUE_OFFICER_ONBOARDED",
            entity_type="SUB_CONSULTANT", entity_id=consultant.id, detail={"username": instance.username},
        )
        response = RevenueOfficerSerializer(instance).data
        if must_change:
            response["generated_password"] = password
            response["_password_warning"] = "Shown once — share it with the account holder now, it cannot be retrieved again."
        return Response(response, status=status.HTTP_201_CREATED)

    @extend_schema(
        parameters=[OpenApiParameter("officer_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=None, responses=RevenueOfficerSerializer,
    )
    @action(
        detail=True, methods=["post"], url_path=r"revenue-officers/(?P<officer_id>[0-9]+)/deactivate",
        # Same set as revenue_officers itself (onboarding and deactivating a
        # login are the same grant, per COUNCIL_IT's whole purpose).
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IT)],
    )
    def deactivate_revenue_officer(self, request, pk=None, officer_id=None):
        """AppUser.is_active is already the login's active flag — a
        deactivated user's Django auth backend already refuses login by
        default, no new auth-layer code needed. Idempotent, same shape as
        APIClientViewSet.revoke."""
        consultant = self.get_object()
        officer = get_object_or_404(AppUser, pk=officer_id, consultant=consultant, role__access_level=AppRole.REVENUE_OFFICER)
        if officer.is_active:
            officer.is_active = False
            officer.save(update_fields=["is_active"])
            audit(
                council_id=consultant.council_id, actor=request.user, action="REVENUE_OFFICER_DEACTIVATED",
                entity_type="APP_USER", entity_id=officer.id, detail={"username": officer.username},
            )
        return Response(RevenueOfficerSerializer(officer).data)

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
        entry = get_object_or_404(ConsultantPortfolio, pk=portfolio_id, consultant=consultant)
        entry.effective_to = timezone.localdate()
        entry.save(update_fields=["effective_to"])
        audit(
            council_id=consultant.council_id, actor=request.user, action="PORTFOLIO_REVOKED", entity_type="SUB_CONSULTANT",
            entity_id=consultant.id, detail={"portfolio_id": entry.id},
        )
        return Response(ConsultantPortfolioSerializer(entry).data)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by agent code or agent name"),
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by status — ACTIVE, SUSPENDED, EXITED"),
            OpenApiParameter("assigned_ward", OpenApiTypes.INT, description="Filter to one ward"),
        ]
    )
)
class FieldAgentViewSet(GeneratedPasswordCreateMixin, viewsets.ModelViewSet):
    """create stays COUNCIL_ADMIN/CONSULTANT/COUNCIL_IT (account-management,
    matching COUNCIL_IT's whole purpose per docs/RBAC_EXPANSION_DESIGN.md);
    list/retrieve widen further to COUNCIL_IT/CONSULTANT_STAFF/
    COUNCIL_AUDITOR/COUNCIL_IGR_HEAD/AGENT_SUPERVISOR — read-only additions,
    see get_permissions(). AGENT_SUPERVISOR's own further narrowing (own
    ward/team only) happens in get_queryset() via common.scoping.

    COUNCIL_IT was originally left off list/retrieve — could create an
    agent but not see the list it just created it into, confirmed live
    against production by the frontend team (2026-09-11 CHANGELOG entry).
    A role that can create a resource always needs to be able to list it;
    that's not a separate grant to weigh, it's the same grant."""

    serializer_class = FieldAgentSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT)]
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"
    # Same StableOrderingFilter shape as SubConsultantViewSet/ReceiptViewSet.
    # "agent_full_name" isn't a real FieldAgent column — FieldAgentSerializer's
    # own agent_full_name aliases user.full_name — so get_queryset annotates
    # it under that exact name (see ReceiptViewSet's comment on why the name
    # has to match literally). This class also has several @action methods
    # (portfolio, activity, deactivate, etc.) that build their own Response
    # directly and never call self.filter_queryset() — class-level
    # filter_backends is inert for those, only list() actually uses it (see
    # the identical finding on SubConsultantViewSet.portfolio).
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["agent_code", "status", "agent_full_name"]

    def get_permissions(self):
        if self.action == "create":
            return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.COUNCIL_IT)()]
        if self.action in ("list", "retrieve"):
            return [access_level_permission(
                AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.COUNCIL_IT, AppRole.CONSULTANT_STAFF,
                AppRole.COUNCIL_AUDITOR, AppRole.COUNCIL_IGR_HEAD, AppRole.AGENT_SUPERVISOR,
            )()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        # FieldAgentSerializer walks user.full_name/phone/consultant_id and
        # assigned_ward on every row — select_related kills the per-row user
        # query (PERF-3).
        qs = (
            FieldAgent.objects.filter(council_id=user.council_id)
            .select_related("user", "assigned_ward")
            .annotate(agent_full_name=models.F("user__full_name"))
        )
        if user.access_level in (AppRole.CONSULTANT, AppRole.CONSULTANT_STAFF):
            qs = qs.filter(user__consultant_id=user.consultant_id)
        elif user.access_level == AppRole.AGENT:
            # Only relevant to the `activity` action below (list/retrieve/
            # create stay staff-only via get_permissions() above) — scoping
            # here too means an agent reaching for another agent's id 404s
            # at get_object(), rather than relying solely on activity()'s
            # own ownership check.
            qs = qs.filter(user_id=user.id)
        elif user.access_level == AppRole.AGENT_SUPERVISOR:
            # Own ward/team only (matrix Decision #4) — reuse
            # portfolio_filter's own AGENT_SUPERVISOR branch by filtering on
            # this FieldAgent row's own assigned_ward directly (there's no
            # payer relation to walk through here, so payer_path is unused;
            # calling portfolio_filter would need a payer hop this model
            # doesn't have, so the ward check is inlined instead).
            ward_id = getattr(getattr(user, "field_agent", None), "assigned_ward_id", None)
            qs = qs.filter(assigned_ward_id=ward_id) if ward_id is not None else qs.none()
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(agent_code__icontains=q) | models.Q(user__full_name__icontains=q))
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        assigned_ward = self.request.query_params.get("assigned_ward")
        if assigned_ward:
            qs = qs.filter(assigned_ward_id=assigned_ward)
        return qs.order_by("agent_code")

    def perform_create(self, serializer):
        user = self.request.user
        data = serializer.validated_data
        agent_role, _ = AppRole.objects.get_or_create(name="FIELD_AGENT", defaults={"access_level": AppRole.AGENT})

        if user.access_level == AppRole.CONSULTANT:
            # Previously unchecked — a PENDING (or SUSPENDED) consultant
            # manager could self-onboard field agents through this branch
            # even though the council-admin-driven branch below always
            # required ACTIVE. Closing that gap: "must be ACTIVE" now holds
            # regardless of who's doing the onboarding.
            if not SubConsultant.objects.filter(id=user.consultant_id, status=SubConsultant.ACTIVE).exists():
                raise serializers.ValidationError({"consultant_id": "Your consultant firm must be ACTIVE before onboarding field agents."})
            consultant_id = user.consultant_id
        else:
            # Council-direct agents (no consultant) are retired — every agent
            # the council itself onboards must be assigned to one, same as a
            # consultant onboarding their own agent always was. Validated
            # here rather than left to the AppUser FK's own constraint, same
            # "pre-check before it becomes an unhandled 500" reasoning as the
            # duplicate contract_ref fix elsewhere in this codebase.
            consultant_id = self.request.data.get("consultant_id")
            if not consultant_id:
                raise serializers.ValidationError({"consultant_id": "Every agent must be assigned to a consultant."})
            if not SubConsultant.objects.filter(id=consultant_id, council_id=user.council_id, status=SubConsultant.ACTIVE).exists():
                raise serializers.ValidationError({"consultant_id": "Not a valid active consultant for this council."})

        password, must_change = provision_password(data.pop("password", None))
        username = data.pop("username")
        full_name = data.pop("full_name")
        phone = data.pop("phone", "")
        if AppUser.objects.filter(username=username).exists():
            raise serializers.ValidationError({"username": "That username is already in use."})
        # AGT-#### is derived from a count that can collide (a previously
        # deleted/retired agent's code leaves a gap) — retry on a unique-code
        # collision (each attempt in its own transaction, so a failed
        # AppUser+FieldAgent pair rolls back whole) before surfacing an error.
        next_seq = FieldAgent.objects.filter(council_id=user.council_id).count() + 1
        for _ in range(20):
            try:
                with transaction.atomic():
                    app_user = AppUser.objects.create_user(
                        username=username,
                        password=password,
                        full_name=full_name,
                        phone=phone,
                        council_id=user.council_id,
                        role=agent_role,
                        consultant_id=consultant_id,
                        must_change_password=must_change,
                    )
                    agent = serializer.save(
                        council_id=user.council_id,
                        user=app_user,
                        agent_code=f"AGT-{next_seq:05d}",
                    )
                break
            except IntegrityError:
                next_seq += 1
        else:
            raise serializers.ValidationError({"agent_code": "Could not allocate a unique agent code — contact support."})
        if must_change:
            self._last_generated_password = password
        audit(
            council_id=user.council_id, actor=user, action="AGENT_ONBOARDED", entity_type="FIELD_AGENT",
            entity_id=agent.id, detail={"agent_code": agent.agent_code},
        )

    @extend_schema(request=FieldAgentStatusSerializer, responses=FieldAgentSerializer)
    @action(
        detail=True, methods=["post"],
        # Same set as create's own permissions — a CONSULTANT can only reach
        # their own agent anyway, via get_queryset()'s scoping above.
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.COUNCIL_IT)],
    )
    def status_change(self, request, pk=None):
        """Mirrors SubConsultantViewSet.status_change's exact shape — same
        3-state field, same audit detail keys, for consistency."""
        agent = self.get_object()
        serializer = FieldAgentStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_status = agent.status
        agent.status = serializer.validated_data["status"]
        agent.save(update_fields=["status", "updated_at"])
        audit(
            council_id=agent.council_id, actor=request.user, action="AGENT_STATUS_CHANGED",
            entity_type="FIELD_AGENT", entity_id=agent.id, detail={"old_status": old_status, "new_status": agent.status},
        )
        return Response(FieldAgentSerializer(agent).data)

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
        entry = get_object_or_404(AgentPortfolio, pk=portfolio_id, agent=agent)
        entry.effective_to = timezone.localdate()
        entry.save(update_fields=["effective_to"])
        audit(
            council_id=agent.council_id, actor=request.user, action="AGENT_PORTFOLIO_REVOKED", entity_type="FIELD_AGENT",
            entity_id=agent.id, detail={"portfolio_id": entry.id},
        )
        return Response(AgentPortfolioSerializer(entry).data)

    @extend_schema(request=AssignPayerSerializer, responses=PayerSerializer)
    @action(
        detail=True, methods=["post"], url_path="assign-payer",
        # Widened for AGENT_SUPERVISOR — matrix Decision #4's "reassign
        # ratepayers/routes" is this action. get_queryset() already scopes a
        # supervisor to their own ward's agents, so reassigning to/from
        # outside that ward 404s before this body runs.
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT_SUPERVISOR)],
    )
    def assign_payer(self, request, pk=None):
        """Hands an already-registered payer to this specific agent —
        get_queryset() already scopes a CONSULTANT caller to their own
        agents, so reaching this for another firm's agent 404s before this
        body runs, same as `portfolio` above. Once assigned, the payer comes
        out of the general consultant-team pool for scoping purposes (see
        apps.common.scoping.portfolio_filter) — only this agent, and the
        consultant manager, see it from here on."""
        agent = self.get_object()
        serializer = AssignPayerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payer = get_object_or_404(Payer, pk=serializer.validated_data["payer_id"], council_id=agent.council_id)

        agent_consultant_id = agent.user.consultant_id
        if agent_consultant_id is not None:
            payer_consultant_id = payer.enumerated_by.consultant_id if payer.enumerated_by_id else None
            if payer_consultant_id != agent_consultant_id:
                return Response(
                    {"error": "This payer isn't in this agent's own consultant's portfolio"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        payer.assigned_agent = agent.user
        payer.save(update_fields=["assigned_agent"])
        audit(
            council_id=agent.council_id, actor=request.user, action="PAYER_ASSIGNED_TO_AGENT", entity_type="PAYER",
            entity_id=payer.id, detail={"agent_id": agent.id, "agent_code": agent.agent_code},
        )
        return Response(PayerSerializer(payer).data)

    @extend_schema(responses=AgentActivityResponseSerializer)
    @action(
        detail=True, methods=["get"],
        # Wider than get_permissions()'s COUNCIL_ADMIN/CONSULTANT default —
        # this is also the mobile agent app's own "today's tally" tile, so
        # an agent needs to read their own activity. get_queryset() already
        # scopes an AGENT caller to their own FieldAgent row (another
        # agent's id 404s before this body runs); the check below is
        # belt-and-suspenders against a future get_queryset() change.
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.AGENT_SUPERVISOR)],
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


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by name or username"),
            OpenApiParameter("is_active", OpenApiTypes.BOOL, description="Filter by account status"),
        ]
    )
)
class StakeholderViewSet(GeneratedPasswordCreateMixin, viewsets.ModelViewSet):
    """Read-only oversight accounts (GLOBAL_VIEW access level) — council/FCT
    stakeholders who need a performance pulse but must never see individual
    payer or sub-consultant identities. That boundary is enforced elsewhere
    (GLOBAL_VIEW is deliberately absent from PayerViewSet, BillViewSet,
    PaymentViewSet, ReceiptViewSet and SubConsultantViewSet's permissions,
    and DashboardGlobalView anonymizes its per-consultant breakdown for this
    role) — this viewset only manages the accounts themselves, and that
    management is COUNCIL_ADMIN-only both ways."""

    serializer_class = StakeholderSerializer
    # COUNCIL_IT included — account creation is its whole purpose (see
    # docs/RBAC_EXPANSION_DESIGN.md); it never gains read access to what a
    # stakeholder actually sees.
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IT)]
    http_method_names = ["get", "post", "head", "options"]
    lookup_value_regex = r"[0-9]+"
    # Added for the Reports page's Stakeholders tab — all three are real
    # AppUser columns, no annotation needed.
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["full_name", "date_joined", "is_active"]

    def get_queryset(self):
        qs = AppUser.objects.filter(council_id=self.request.user.council_id, role__access_level=AppRole.GLOBAL_VIEW)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(models.Q(full_name__icontains=q) | models.Q(username__icontains=q))
        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ("1", "true"))
        return qs.order_by("full_name")

    def perform_create(self, serializer):
        user = self.request.user
        data = serializer.validated_data
        stakeholder_role, _ = AppRole.objects.get_or_create(name="STAKEHOLDER", defaults={"access_level": AppRole.GLOBAL_VIEW})
        password, must_change = provision_password(data.pop("password", None))
        instance = AppUser.objects.create_user(
            username=data.pop("username"),
            password=password,
            full_name=data.pop("full_name"),
            phone=data.pop("phone", ""),
            council_id=user.council_id,
            role=stakeholder_role,
            must_change_password=must_change,
        )
        if must_change:
            self._last_generated_password = password
        serializer.instance = instance
        audit(
            council_id=user.council_id, actor=user, action="STAKEHOLDER_ONBOARDED",
            entity_type="APP_USER", entity_id=instance.id, detail={"username": instance.username},
        )

    @extend_schema(request=None, responses=StakeholderSerializer)
    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        """Same dormant-is_active/idempotent shape as SubConsultantViewSet.
        deactivate_revenue_officer — see that action's own docstring."""
        stakeholder = self.get_object()
        if stakeholder.is_active:
            stakeholder.is_active = False
            stakeholder.save(update_fields=["is_active"])
            audit(
                council_id=stakeholder.council_id, actor=request.user, action="STAKEHOLDER_DEACTIVATED",
                entity_type="APP_USER", entity_id=stakeholder.id, detail={"username": stakeholder.username},
            )
        return Response(StakeholderSerializer(stakeholder).data)
