from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.accounts.models import AppRole
from apps.audit.services import audit
from apps.common.filtering import parse_int as parse_int_params
from apps.common.permissions import access_level_permission
from apps.revenue.api.serializers import (
    ChangeRateSerializer,
    CouncilRevenueItemSerializer,
    CreateCouncilRevenueItemSerializer,
    ReplaceRateBandsSerializer,
    RevenueCategorySerializer,
    RevenueItemTemplateSerializer,
    SetDepartmentSerializer,
)
from apps.revenue.models import (
    AgentPortfolio,
    CouncilRevenueItem,
    RateBand,
    RateSchedule,
    RevenueCategory,
    RevenueItemTemplate,
)
from apps.revenue.services import BandingError, RetireError, change_rate, create_revenue_item, replace_rate_bands, retire_revenue_item
from apps.tenancy.models import Department

READ_ONLY_LEVELS = [
    AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.GLOBAL_VIEW, AppRole.REVENUE_OFFICER,
    # RBAC-expansion council/consultant/agent-tier read additions — see
    # docs/RBAC_EXPANSION_DESIGN.md. Revenue items aren't payer- or
    # consultant-identifying (see CouncilRevenueItemViewSet's own docstring
    # on why GLOBAL_VIEW already keeps this), so every new read-only role is
    # safe to add here without the exclusions PayerViewSet/BillViewSet need.
    AppRole.COUNCIL_IGR_HEAD, AppRole.COUNCIL_TREASURY, AppRole.COUNCIL_AUDITOR, AppRole.COUNCIL_IT,
    AppRole.CONSULTANT_STAFF, AppRole.AGENT_SUPERVISOR,
]
_PORTFOLIO_SCOPED_LEVELS = (AppRole.CONSULTANT, AppRole.CONSULTANT_STAFF, AppRole.REVENUE_OFFICER)


class RevenueCategoryViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RevenueCategorySerializer
    queryset = RevenueCategory.objects.all()
    permission_classes = [access_level_permission(*READ_ONLY_LEVELS)]


class RevenueItemTemplateViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RevenueItemTemplateSerializer
    # select_related("category") — the serializer reads category.name per row.
    queryset = RevenueItemTemplate.objects.select_related("category")
    permission_classes = [access_level_permission(*READ_ONLY_LEVELS)]


class CouncilRevenueItemViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    serializer_class = CouncilRevenueItemSerializer
    permission_classes = [access_level_permission(*READ_ONLY_LEVELS)]
    lookup_value_regex = r"[0-9]+"

    def get_permissions(self):
        if self.action in ("create", "destroy", "retire"):
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        return super().get_permissions()

    def get_queryset(self):
        # select_related + prefetch_related (with to_attr, which the model's
        # current_rate/active_bands properties check for — see their
        # docstrings) avoid the N+1 storm this list serializes into
        # otherwise: category/department were one query per item, and
        # current_rate/active_bands were two more per item plus one per band
        # for its tiers — 650+ queries and a 32s gunicorn-timeout 500 once
        # the full gazette catalogue (46 items, 474 bands, 336 tiers) was
        # seeded, versus a handful of flat illustrative items before that.
        qs = (
            CouncilRevenueItem.objects.filter(council_id=self.request.user.council_id, is_active=True)
            .select_related("category", "department")
            .prefetch_related(
                Prefetch(
                    "rate_schedules",
                    queryset=RateSchedule.objects.filter(effective_to__isnull=True).order_by("-effective_from"),
                    to_attr="_prefetched_current_rate",
                ),
                Prefetch(
                    "rate_bands",
                    queryset=RateBand.objects.filter(effective_to__isnull=True)
                    .order_by("sort_order", "label")
                    .prefetch_related("tiers"),
                    to_attr="_prefetched_active_bands",
                ),
            )
            .order_by("harmonised_code")
        )
        department_param = parse_int_params(self.request.query_params, "department")
        if department_param is not None:
            qs = qs.filter(department_id=department_param)
        # Unlike payers/bills/payments (scoped via common.scoping.portfolio_filter,
        # which walks a payer's enumerated_by__consultant_id), a revenue item has
        # no payer to walk through — its portfolio membership is the direct
        # ConsultantPortfolio join instead. COUNCIL_ADMIN/AGENT/GLOBAL_VIEW stay
        # unscoped: an item's existence/rate isn't payer- or consultant-identifying
        # the way a name is, so GLOBAL_VIEW keeping this is deliberate, matching
        # DashboardSummaryView/DashboardGlobalView's aggregate-only boundary.
        if self.request.user.access_level in _PORTFOLIO_SCOPED_LEVELS:
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

    @extend_schema(request=CreateCouncilRevenueItemSerializer, responses=CouncilRevenueItemSerializer)
    def create(self, request, *args, **kwargs):
        """Only COUNCIL_ADMIN may manually create a council-local revenue item."""
        serializer = CreateCouncilRevenueItemSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        category = get_object_or_404(RevenueCategory, pk=data["category_id"])
        department = None
        if data.get("department_id") is not None:
            department = get_object_or_404(Department, pk=data["department_id"], council_id=request.user.council_id)

        item = create_revenue_item(
            council=request.user.council,
            harmonised_code=data["harmonised_code"],
            item_name=data["item_name"],
            category=category,
            unit_of_charge=data["unit_of_charge"],
            rate_amount=data["rate_amount"],
            actor=request.user,
            department=department,
            bye_law_reference=data.get("bye_law_reference", ""),
            bye_law_description=data.get("bye_law_description", ""),
        )
        return Response(CouncilRevenueItemSerializer(item).data, status=status.HTTP_201_CREATED)

    def _retire(self, request):
        """Shared by destroy() and retire() — same operation, two entry points
        (REST DELETE and an explicit action), so the error handling and
        response shape stay in one place rather than drifting apart."""
        item = self.get_object()
        try:
            retire_revenue_item(council_revenue_item=item, actor=request.user)
        except RetireError as exc:
            return None, Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
        return item, None

    def destroy(self, request, *args, **kwargs):
        """Only COUNCIL_ADMIN may retire a revenue item (DELETE /api/v1/revenue-items/{id})."""
        item, error_response = self._retire(request)
        if error_response is not None:
            return error_response
        return Response(status=status.HTTP_204_NO_CONTENT)

    # get_permissions() above already forces COUNCIL_ADMIN for this action
    # unconditionally — no permission_classes kwarg here, since one would
    # never actually be consulted and could silently drift from it.
    @extend_schema(request=None, responses=CouncilRevenueItemSerializer)
    @action(detail=True, methods=["post"], url_path="retire")
    def retire(self, request, pk=None):
        """Only COUNCIL_ADMIN may retire a revenue item (POST /api/v1/revenue-items/{id}/retire)."""
        item, error_response = self._retire(request)
        if error_response is not None:
            return error_response
        # retire_revenue_item() mutates and saves this exact instance already
        # (same object reference get_object() returned) — no need to re-fetch.
        return Response(CouncilRevenueItemSerializer(item).data)

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

    @extend_schema(request=SetDepartmentSerializer, responses=CouncilRevenueItemSerializer)
    @action(detail=True, methods=["post"], url_path="department", permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def department(self, request, pk=None):
        """Only COUNCIL_ADMIN may (re)assign which department an item belongs
        to — pass department_id: null to clear it."""
        item = self.get_object()
        serializer = SetDepartmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        department_id = serializer.validated_data["department_id"]
        if department_id is None:
            item.department = None
        else:
            item.department = get_object_or_404(Department, pk=department_id, council_id=request.user.council_id)
        item.save(update_fields=["department"])
        audit(
            council_id=request.user.council_id, actor=request.user, action="REVENUE_ITEM_DEPARTMENT_CHANGED",
            entity_type="COUNCIL_REVENUE_ITEM", entity_id=item.id, detail={"department_id": department_id},
        )
        return Response(CouncilRevenueItemSerializer(item).data)

