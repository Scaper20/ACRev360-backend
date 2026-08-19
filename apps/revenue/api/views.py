from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.common.permissions import access_level_permission
from apps.revenue.api.serializers import (
    ChangeRateSerializer,
    CouncilRevenueItemSerializer,
    ReplaceRateBandsSerializer,
    RevenueCategorySerializer,
    RevenueItemTemplateSerializer,
)
from apps.revenue.models import CouncilRevenueItem, RevenueCategory, RevenueItemTemplate
from apps.revenue.services import BandingError, change_rate, replace_rate_bands

READ_ONLY_LEVELS = [AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW]


class RevenueCategoryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RevenueCategorySerializer
    queryset = RevenueCategory.objects.all()
    permission_classes = [access_level_permission(*READ_ONLY_LEVELS)]


class RevenueItemTemplateViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RevenueItemTemplateSerializer
    queryset = RevenueItemTemplate.objects.all()
    permission_classes = [access_level_permission(*READ_ONLY_LEVELS)]


class CouncilRevenueItemViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CouncilRevenueItemSerializer
    permission_classes = [access_level_permission(*READ_ONLY_LEVELS)]
    lookup_value_regex = r"[0-9]+"

    def get_queryset(self):
        return CouncilRevenueItem.objects.filter(council_id=self.request.user.council_id, is_active=True).order_by("harmonised_code")

    @extend_schema(request=ChangeRateSerializer, responses=CouncilRevenueItemSerializer)
    @action(detail=True, methods=["post"], url_path="rate", permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def rate(self, request, pk=None):
        """Only COUNCIL_ADMIN may change what an item costs — PRD.md §4.1."""
        item = self.get_object()
        serializer = ChangeRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_rate(council_revenue_item=item, new_amount=serializer.validated_data["rate_amount"], actor=request.user)
        item.refresh_from_db()
        return Response(CouncilRevenueItemSerializer(item).data)

    @extend_schema(request=ReplaceRateBandsSerializer, responses=CouncilRevenueItemSerializer)
    @action(detail=True, methods=["post"], url_path="rate-bands", permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def rate_bands(self, request, pk=None):
        """Replaces this item's whole band set — see replace_rate_bands. Posting
        {"bands": []} clears banding and reverts the item to plain FLAT pricing."""
        item = self.get_object()
        serializer = ReplaceRateBandsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            replace_rate_bands(council_revenue_item=item, bands=serializer.validated_data["bands"], actor=request.user)
        except BandingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        item.refresh_from_db()
        return Response(CouncilRevenueItemSerializer(item).data)
