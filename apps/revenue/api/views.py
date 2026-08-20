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
from apps.revenue.models import AgentPortfolio, CouncilRevenueItem, RevenueCategory, RevenueItemTemplate
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
        qs = CouncilRevenueItem.objects.filter(council_id=self.request.user.council_id, is_active=True).order_by("harmonised_code")
        # Unlike payers/bills/payments (scoped via common.scoping.portfolio_filter,
        # which walks a payer's enumerated_by__consultant_id), a revenue item has
        # no payer to walk through — its portfolio membership is the direct
        # ConsultantPortfolio join instead. COUNCIL_ADMIN/AGENT/GLOBAL_VIEW stay
        # unscoped: an item's existence/rate isn't payer- or consultant-identifying
        # the way a name is, so GLOBAL_VIEW keeping this is deliberate, matching
        # DashboardSummaryView/DashboardGlobalView's aggregate-only boundary.
        if self.request.user.access_level == AppRole.CONSULTANT:
            qs = qs.filter(
                portfolio_entries__consultant_id=self.request.user.consultant_id,
                portfolio_entries__effective_to__isnull=True,
            ).distinct()
        elif self.request.user.access_level == AppRole.AGENT:
            # AgentPortfolio is an *optional* further narrowing (see its
            # docstring) — an agent with no rows there isn't locked out, they
            # just inherit their whole consultant's portfolio (or the full
            # catalog if council-direct), same as before this ever existed.
            has_own_portfolio = AgentPortfolio.objects.filter(
                agent__user_id=self.request.user.id, effective_to__isnull=True,
            ).exists()
            if has_own_portfolio:
                qs = qs.filter(
                    agent_portfolio_entries__agent__user_id=self.request.user.id,
                    agent_portfolio_entries__effective_to__isnull=True,
                ).distinct()
            elif self.request.user.consultant_id is not None:
                qs = qs.filter(
                    portfolio_entries__consultant_id=self.request.user.consultant_id,
                    portfolio_entries__effective_to__isnull=True,
                ).distinct()
        return qs

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
