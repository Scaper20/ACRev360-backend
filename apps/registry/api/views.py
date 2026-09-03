from django.db import transaction
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole, SubConsultant
from apps.audit.services import audit
from apps.billing.models import Assessment, Bill
from apps.billing.services import BillingError
from apps.common.filtering import StableOrderingFilter, apply_date_range, apply_payer_dimension_filters
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.registry.api.serializers import (
    CreatePayerSerializer,
    DraftAssessmentSerializer,
    DuplicatePayerResponseSerializer,
    EnumeratedAssetSerializer,
    KycStatusSerializer,
    PayerCreateResponseSerializer,
    PayerSerializer,
)
from apps.registry.models import EnumeratedAsset, Payer
from apps.registry.services import DuplicatePayer, create_payer
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
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER)]
    lookup_value_regex = r"[0-9]+"
    # Set per-view rather than as a DEFAULT_FILTER_BACKEND — a global default
    # would silently change every other list endpoint's behavior too.
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["full_name", "created_at", "payer_ref", "kyc_status"]

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
        qs = Payer.objects.filter(council_id=self.request.user.council_id).order_by("full_name")
        qs = portfolio_filter(qs, self.request, payer_path="")  # payer IS the root here
        params = self.request.query_params
        # Layered on top of portfolio_filter above, never instead of it — see
        # apply_payer_dimension_filters' docstring.
        qs = apply_payer_dimension_filters(qs, params, payer_path="")
        qs = apply_date_range(qs, params, field="created_at")
        q = params.get("q")
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(payer_ref__icontains=q) | Q(phone__icontains=q))
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


class EnumeratedAssetViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EnumeratedAssetSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)]

    def get_queryset(self):
        return EnumeratedAsset.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        serializer.save(council_id=self.request.user.council_id)
