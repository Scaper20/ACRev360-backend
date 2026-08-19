from django.db import transaction
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.audit.services import audit
from apps.billing.models import Assessment, Bill
from apps.billing.services import BillingError
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
        ]
    )
)
class PayerViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW)]
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return super().get_permissions()

    def get_serializer_class(self):
        return CreatePayerSerializer if self.request.method == "POST" else PayerSerializer

    def get_queryset(self):
        qs = Payer.objects.filter(council_id=self.request.user.council_id).order_by("full_name")
        qs = portfolio_filter(qs, self.request, payer_path="")  # payer IS the root here
        q = self.request.query_params.get("q")
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

        items = list(
            CouncilRevenueItem.objects.filter(id__in=revenue_item_ids, council_id=request.user.council_id)
        )

        try:
            payer, draft_count = create_payer(
                council_id=request.user.council_id, actor=request.user, revenue_item_ids=items, force=force, **data
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
