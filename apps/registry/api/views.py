from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole, AppUser, SubConsultant
from apps.audit.services import audit
from apps.billing.api.serializers import BillSerializer
from apps.billing.models import Assessment, Bill
from apps.billing.services import BillingError
from apps.common.filtering import StableOrderingFilter, apply_date_range, apply_payer_dimension_filters, name_search_q
from apps.common.permissions import IsRatepayerOrDelegate, access_level_permission
from apps.common.scoping import portfolio_filter
from apps.payments.api.serializers import PaymentSerializer, ReceiptSerializer
from apps.payments.models import Payment, Receipt
from apps.registry.api.serializers import (
    CreateDelegationSerializer,
    CreatePayerSerializer,
    DraftAssessmentSerializer,
    DuplicatePayerResponseSerializer,
    EnumeratedAssetSerializer,
    InviteRatepayerSerializer,
    KycStatusSerializer,
    PayerCreateResponseSerializer,
    PayerDelegationSerializer,
    PayerSerializer,
)
from apps.registry.models import EnumeratedAsset, Payer, PayerDelegation
from apps.registry.services import DuplicatePayer, accessible_payer_ids, create_payer
from apps.revenue.models import CouncilRevenueItem


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by name, reference or phone"),
            OpenApiParameter("ward_id", OpenApiTypes.INT, description="Filter by the payer's ward"),
            OpenApiParameter(
                "consultant_id", OpenApiTypes.INT,
                description="Filter by the consultant whose user enumerated the payer. Narrows within the "
                "caller's own scope — it never widens it for a CONSULTANT/REVENUE_OFFICER.",
            ),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Registered on/after this date (inclusive)"),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Registered on/before this date (inclusive)"),
        ]
    )
)
class PayerViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    # GLOBAL_VIEW deliberately excluded — the payer registry is names, phone
    # numbers and KYC status, exactly what a stakeholder account must not see.
    # REVENUE_OFFICER is included here (list/retrieve) but excluded again in
    # get_permissions() below for create/kyc_status/DELETE — read-only, same
    # portfolio as CONSULTANT (see common.scoping.portfolio_filter).
    #
    # RBAC-expansion additions (docs/RBAC_EXPANSION_DESIGN.md) — read-only
    # for the same structural reason (get_permissions() below never widens
    # create/kyc_status/DELETE beyond the original four). COUNCIL_IT is
    # deliberately NOT included: its job is account management (agents/
    # officers/stakeholders/ratepayers), never payer PII — this was missed
    # entirely in the first pass (every one of these five levels 403'd on
    # this viewset despite already reading bills/payments/receipts that
    # embed the same payer's name/ref), confirmed live against production
    # by the frontend team; see the 2026-09-11 CHANGELOG entry.
    permission_classes = [access_level_permission(
        AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER,
        AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR,
        AppRole.CONSULTANT_STAFF, AppRole.AGENT_SUPERVISOR,
    )]
    lookup_value_regex = r"[0-9]+"
    # Set per-view rather than as a DEFAULT_FILTER_BACKEND — a global default
    # would silently change every other list endpoint's behavior too.
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["last_name", "first_name", "created_at", "payer_ref", "kyc_status"]

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        # self.action, not self.request.method — kyc_status is also a POST,
        # with its own narrower COUNCIL_ADMIN-only permission_classes on the
        # @action itself; branching on method here would silently override
        # that to this wider list instead of falling through to it.
        if self.action == "create":
            return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)()]
        return super().get_permissions()

    def get_serializer_class(self):
        return CreatePayerSerializer if self.request.method == "POST" else PayerSerializer

    def get_queryset(self):
        qs = Payer.objects.filter(council_id=self.request.user.council_id).order_by("last_name", "first_name")
        qs = portfolio_filter(qs, self.request, payer_path="")  # payer IS the root here
        params = self.request.query_params
        # Layered on top of portfolio_filter above, never instead of it — see
        # apply_payer_dimension_filters' docstring.
        qs = apply_payer_dimension_filters(qs, params, payer_path="")
        qs = apply_date_range(qs, params, field="created_at")
        q = params.get("q")
        if q:
            qs = qs.filter(name_search_q(q) | Q(payer_ref__icontains=q) | Q(phone__icontains=q))
        return qs

    @extend_schema(responses={201: PayerCreateResponseSerializer, 409: DuplicatePayerResponseSerializer})
    def create(self, request, *args, **kwargs):
        serializer = CreatePayerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        revenue_item_ids = data.pop("revenue_item_ids", [])
        force = data.pop("force", False)
        # Admin-only, same "silently ignore rather than error" handling as
        # UpdateProfileSerializer gives other admin-managed fields a
        # non-admin caller has no business setting.
        assigned_consultant_id = data.pop("assigned_consultant_id", None)
        enumerated_by = None
        if assigned_consultant_id and request.user.access_level == AppRole.COUNCIL_ADMIN:
            consultant = SubConsultant.objects.filter(
                id=assigned_consultant_id, council_id=request.user.council_id, status=SubConsultant.ACTIVE,
            ).first()
            if consultant is None:
                return Response({"error": "Not a valid active consultant for this council."}, status=status.HTTP_400_BAD_REQUEST)
            enumerated_by = consultant.users.filter(is_active=True).first()
            if enumerated_by is None:
                return Response({"error": "This consultant has no linked login yet — cannot assign payers to it."}, status=status.HTTP_400_BAD_REQUEST)

        items = list(
            CouncilRevenueItem.objects.filter(id__in=revenue_item_ids, council_id=request.user.council_id)
        )

        try:
            payer, draft_count = create_payer(
                council_id=request.user.council_id, actor=request.user, revenue_item_ids=items, force=force,
                enumerated_by=enumerated_by, **data
            )
        except DuplicatePayer as exc:
            return Response(
                {"error": str(exc), "duplicate_of": PayerSerializer(exc.duplicate_of).data},
                status=status.HTTP_409_CONFLICT,
            )
        except BillingError as exc:
            # A checked revenue_item_id needs a rate band the caller never
            # supplied (see CouncilRevenueItem.active_bands) — surface this
            # as a normal 400 instead of an unhandled 500. The frontend's
            # enumeration checklist excludes banded items for exactly this
            # reason; this remains as a backstop for any other caller (e.g.
            # a future offline-sync replay) that might still hit it.
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = PayerSerializer(payer).data
        payload["draft_assessments_created"] = draft_count
        return Response(payload, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        payer = self.get_object()
        if Bill.objects.filter(payer=payer).exists():
            return Response(
                {"error": f"{payer.payer_ref} has bills on record and can't be deleted — this preserves the billing history."},
                status=status.HTTP_409_CONFLICT,
            )
        audit(
            council_id=payer.council_id, actor=request.user, action="PAYER_DELETED", entity_type="PAYER",
            entity_id=payer.id, detail={"payer_ref": payer.payer_ref, "full_name": payer.full_name},
        )
        return super().destroy(request, *args, **kwargs)

    @extend_schema(responses=DraftAssessmentSerializer(many=True))
    @action(detail=True, methods=["get"], url_path="draft-assessments", pagination_class=None)
    def draft_assessments(self, request, pk=None):
        payer = self.get_object()
        drafts = Assessment.objects.filter(payer=payer, status=Assessment.DRAFT).select_related("council_revenue_item")
        return Response([
            {
                "id": a.id,
                "council_revenue_item_id": a.council_revenue_item_id,
                "harmonised_code": a.council_revenue_item.harmonised_code,
                "item_name": a.council_revenue_item.item_name,
                "quantity": a.quantity,
                "amount": a.amount,
            }
            for a in drafts
        ])

    @extend_schema(request=KycStatusSerializer, responses=PayerSerializer)
    @action(detail=True, methods=["post"], url_path="kyc-status", permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def kyc_status(self, request, pk=None):
        """Only COUNCIL_ADMIN may move a payer through KYC review — there was
        previously no path to this at all (kyc_status was write-once at
        creation, always PENDING)."""
        payer = self.get_object()
        serializer = KycStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        old_status = payer.kyc_status
        payer.kyc_status = serializer.validated_data["kyc_status"]
        payer.save(update_fields=["kyc_status"])
        audit(
            council_id=payer.council_id, actor=request.user, action="PAYER_KYC_STATUS_CHANGED", entity_type="PAYER",
            entity_id=payer.id, detail={"old_status": old_status, "new_status": payer.kyc_status},
        )
        return Response(PayerSerializer(payer).data)

    @extend_schema(request=InviteRatepayerSerializer, responses=PayerSerializer)
    @action(
        detail=True, methods=["post"], url_path="invite-ratepayer",
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.COUNCIL_IT, AppRole.AGENT)],
    )
    def invite_ratepayer(self, request, pk=None):
        """Sets up this payer's own self-service login (RatepayerPortalViewSet)
        — staff-invoked only, never public self-registration (see
        docs/RBAC_EXPANSION_DESIGN.md's deliberately-not-built list: no
        identity-verification design exists for an open signup flow)."""
        payer = self.get_object()
        if payer.user_id is not None:
            return Response({"error": f"{payer.payer_ref} already has a ratepayer login."}, status=status.HTTP_409_CONFLICT)
        serializer = InviteRatepayerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role, _created = AppRole.objects.get_or_create(name="RATEPAYER", defaults={"access_level": AppRole.RATEPAYER})
        user = AppUser.objects.create_user(
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
            full_name=payer.full_name,
            council_id=payer.council_id,
            role=role,
        )
        payer.user = user
        payer.save(update_fields=["user"])
        audit(
            council_id=payer.council_id, actor=request.user, action="RATEPAYER_LOGIN_CREATED", entity_type="PAYER",
            entity_id=payer.id, detail={"payer_ref": payer.payer_ref, "username": user.username},
        )
        return Response(PayerSerializer(payer).data, status=status.HTTP_201_CREATED)


class RatepayerPortalViewSet(viewsets.GenericViewSet):
    """Self-service endpoints for RATEPAYER/RATEPAYER_PROXY logins — read-only
    access to one's own (or delegated) bills/payments/receipts, plus
    managing who's delegated. See docs/RBAC_EXPANSION_DESIGN.md.

    Deliberately not a ModelViewSet against Payer itself — a ratepayer has
    no business editing their own registry record (KYC, ward, etc. stay
    staff-managed); this only ever reads Bill/Payment/Receipt rows that
    already exist, scoped through accessible_payer_ids.

    No detail=True action here uses get_object()/self.queryset — every
    action resolves its own rows directly through accessible_payer_ids, so
    there's nothing to accidentally leave un-scoped by forgetting to
    override get_queryset() on a mixin."""

    permission_classes = [IsRatepayerOrDelegate]
    # Fallback for drf-spectacular's schema introspection on the detail=True
    # revoke route below (which has no @extend_schema of its own) — every
    # other action declares its own response type explicitly.
    serializer_class = PayerDelegationSerializer
    lookup_value_regex = r"[0-9]+"

    def _own_council_and_payers(self, request):
        return request.user.council_id, accessible_payer_ids(request.user)

    @extend_schema(responses=BillSerializer(many=True))
    @action(detail=False, methods=["get"])
    def bills(self, request):
        council_id, payer_ids = self._own_council_and_payers(request)
        qs = Bill.objects.filter(council_id=council_id, payer_id__in=payer_ids).order_by("-created_at")
        return Response(BillSerializer(qs, many=True).data)

    @extend_schema(responses=PaymentSerializer(many=True))
    @action(detail=False, methods=["get"])
    def payments(self, request):
        council_id, payer_ids = self._own_council_and_payers(request)
        qs = Payment.objects.filter(council_id=council_id, bill__payer_id__in=payer_ids).order_by("-created_at")
        return Response(PaymentSerializer(qs, many=True).data)

    @extend_schema(responses=ReceiptSerializer(many=True))
    @action(detail=False, methods=["get"])
    def receipts(self, request):
        council_id, payer_ids = self._own_council_and_payers(request)
        qs = Receipt.objects.filter(council_id=council_id, payment__bill__payer_id__in=payer_ids).order_by("-created_at")
        return Response(ReceiptSerializer(qs, many=True).data)

    @extend_schema(request=CreateDelegationSerializer, responses=PayerDelegationSerializer(many=True))
    @action(detail=False, methods=["get", "post"])
    def delegations(self, request):
        """RATEPAYER only, never RATEPAYER_PROXY — matrix Decision #2's
        "ratepayer explicitly grants/revokes" means the account being
        delegated is the one in control, not the delegate."""
        if request.user.access_level != AppRole.RATEPAYER:
            return Response({"error": "Only the ratepayer themselves may manage delegations."}, status=status.HTTP_403_FORBIDDEN)
        payer = getattr(request.user, "payer_profile", None)
        if payer is None:
            return Response({"error": "No linked payer account."}, status=status.HTTP_400_BAD_REQUEST)

        if request.method == "GET":
            qs = payer.delegations.filter(revoked_at__isnull=True)
            return Response(PayerDelegationSerializer(qs, many=True).data)

        serializer = CreateDelegationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # council_id=payer.council_id is deliberate, not redundant with RLS:
        # app_user carries no RLS policy at all (see docs/RBAC_EXPANSION_DESIGN.md
        # / the tenancy survey — app_role/app_user/council_grant are the tables
        # that sit outside RLS), so an unscoped email lookup here would let a
        # ratepayer probe whether an email belongs to a RATEPAYER_PROXY account
        # in *any* council, not just their own — a user-enumeration leak across
        # the tenant boundary even though the resulting delegation could never
        # actually read anything (bills()/payments()/receipts() below filter by
        # council_id too, so a cross-council proxy_user would just always see
        # zero rows). Scoping the lookup itself closes the leak instead of
        # relying on that downstream filter to make it harmless.
        proxy = AppUser.objects.filter(
            email__iexact=serializer.validated_data["proxy_email"], role__access_level=AppRole.RATEPAYER_PROXY,
            council_id=payer.council_id,
        ).first()
        if proxy is None:
            return Response({"error": "No ratepayer-proxy account found with that email."}, status=status.HTTP_400_BAD_REQUEST)

        existing = PayerDelegation.objects.filter(payer=payer, proxy_user=proxy, revoked_at__isnull=True).first()
        if existing:
            return Response(PayerDelegationSerializer(existing).data, status=status.HTTP_200_OK)
        delegation = PayerDelegation.objects.create(
            council_id=payer.council_id, payer=payer, proxy_user=proxy, granted_by=request.user,
        )
        audit(
            council_id=payer.council_id, actor=request.user, action="PAYER_DELEGATION_GRANTED", entity_type="PAYER",
            entity_id=payer.id, detail={"proxy_user_id": proxy.id, "proxy_email": proxy.email},
        )
        return Response(PayerDelegationSerializer(delegation).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses=PayerDelegationSerializer)
    @action(detail=True, methods=["post"], url_path="revoke")
    def revoke_delegation(self, request, pk=None):
        if request.user.access_level != AppRole.RATEPAYER:
            return Response({"error": "Only the ratepayer themselves may revoke a delegation."}, status=status.HTTP_403_FORBIDDEN)
        payer = getattr(request.user, "payer_profile", None)
        delegation = PayerDelegation.objects.filter(pk=pk, payer=payer, revoked_at__isnull=True).first() if payer else None
        if delegation is None:
            return Response({"error": "Delegation not found."}, status=status.HTTP_404_NOT_FOUND)
        delegation.revoked_at = timezone.now()
        delegation.save(update_fields=["revoked_at"])
        audit(
            council_id=payer.council_id, actor=request.user, action="PAYER_DELEGATION_REVOKED", entity_type="PAYER",
            entity_id=payer.id, detail={"proxy_user_id": delegation.proxy_user_id},
        )
        return Response(PayerDelegationSerializer(delegation).data)


class EnumeratedAssetViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EnumeratedAssetSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)]

    def get_queryset(self):
        return EnumeratedAsset.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        serializer.save(council_id=self.request.user.council_id)
