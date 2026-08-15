from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.billing.models import Assessment
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.registry.api.serializers import CreatePayerSerializer, EnumeratedAssetSerializer, PayerSerializer
from apps.registry.models import EnumeratedAsset, Payer
from apps.registry.services import DuplicatePayer, create_payer
from apps.revenue.models import CouncilRevenueItem


class PayerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW)]

    def get_serializer_class(self):
        return CreatePayerSerializer if self.request.method == "POST" else PayerSerializer

    def get_queryset(self):
        qs = Payer.objects.filter(council_id=self.request.user.council_id).order_by("full_name")
        qs = portfolio_filter(qs, self.request, payer_path="")  # payer IS the root here
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(payer_ref__icontains=q) | Q(phone__icontains=q))
        return qs

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

        payload = PayerSerializer(payer).data
        payload["draft_assessments_created"] = draft_count
        return Response(payload, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="draft-assessments")
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


class EnumeratedAssetViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = EnumeratedAssetSerializer
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)]

    def get_queryset(self):
        return EnumeratedAsset.objects.filter(council_id=self.request.user.council_id)

    def perform_create(self, serializer):
        serializer.save(council_id=self.request.user.council_id)
