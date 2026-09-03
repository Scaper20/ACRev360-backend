from django.db import models, transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AppRole
from apps.billing.api.serializers import (
    AddLineSerializer,
    BillDetailSerializer,
    BillLineDetailSerializer,
    BillSerializer,
    IssueBillSerializer,
    PublicBillLookupSerializer,
    SupersededBillSerializer,
    UpdateLineSerializer,
)
from apps.audit.services import audit
from apps.billing.models import Assessment, Bill, BillLine
from apps.billing.services import BillingError, add_bill_line, delete_bill_line, issue_bill, update_bill_line
from apps.common.filtering import (
    StableOrderingFilter,
    apply_date_range,
    apply_payer_dimension_filters,
    parse_decimal,
    parse_int,
)
from apps.common.permissions import access_level_permission
from apps.common.scoping import portfolio_filter
from apps.payments.models import Payment
from apps.registry.models import Payer
from apps.revenue.models import CouncilRevenueItem, RateBand, RateTier
from apps.tenancy.context import council_context


def _resolve_band_and_tier(entry, item):
    """Turns rate_band_id/rate_tier_id off a validated line entry into real
    model instances, 404ing on a mismatched id rather than letting the billing
    service silently ignore one it can't find. Tenancy scoping goes through
    council_revenue_item (already council-scoped by the caller), since
    RateBand/RateTier aren't CouncilScopedModels themselves — they inherit
    tenancy from the item they price."""
    rate_band = None
    if entry.get("rate_band_id") is not None:
        rate_band = get_object_or_404(RateBand, pk=entry["rate_band_id"], council_revenue_item=item)
    rate_tier = None
    if entry.get("rate_tier_id") is not None:
        rate_tier = get_object_or_404(RateTier, pk=entry["rate_tier_id"], band__council_revenue_item=item)
    return rate_band, rate_tier


class IssueBillResponseSerializer(BillSerializer):
    arrears_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    superseded_count = serializers.IntegerField(read_only=True)

    class Meta(BillSerializer.Meta):
        fields = BillSerializer.Meta.fields + ["arrears_amount", "superseded_count"]


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, description="Filter by bill status"),
            OpenApiParameter("payer", OpenApiTypes.INT, description="Filter to one payer's bills"),
            OpenApiParameter("q", OpenApiTypes.STR, description="Search by bill reference or payer name"),
            OpenApiParameter("ward_id", OpenApiTypes.INT, description="Filter by the bill's payer's ward"),
            OpenApiParameter(
                "consultant_id", OpenApiTypes.INT,
                description="Filter by the consultant whose user enumerated the bill's payer. Narrows within "
                "the caller's own scope — it never widens it for a CONSULTANT/REVENUE_OFFICER.",
            ),
            OpenApiParameter("revenue_item_id", OpenApiTypes.INT, description="Bills carrying a line for this revenue item"),
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Issued on/after this date (inclusive)"),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Issued on/before this date (inclusive)"),
            OpenApiParameter("value_min", OpenApiTypes.NUMBER, description="Minimum total_amount (inclusive)"),
            OpenApiParameter("value_max", OpenApiTypes.NUMBER, description="Maximum total_amount (inclusive)"),
        ]
    ),
    create=extend_schema(request=IssueBillSerializer, responses=IssueBillResponseSerializer),
)
class BillViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    # GLOBAL_VIEW deliberately excluded — bills carry payer full_name/payer_ref,
    # exactly what a stakeholder account must not see (aggregate totals only,
    # via DashboardSummaryView/DashboardGlobalView). REVENUE_OFFICER is
    # included here (list/retrieve/bill_detail) but explicitly excluded again
    # in get_permissions() below for POST/DELETE — read-only, same portfolio
    # as CONSULTANT (see common.scoping.portfolio_filter).
    permission_classes = [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT, AppRole.REVENUE_OFFICER)]
    lookup_value_regex = r"[0-9]+"
    # Per-view, not a DEFAULT_FILTER_BACKEND — see PayerViewSet's identical note.
    filter_backends = [StableOrderingFilter]
    ordering_fields = ["total_amount", "due_date", "bill_ref", "created_at", "amount_paid", "status"]

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [access_level_permission(AppRole.COUNCIL_ADMIN)()]
        # self.action, not self.request.method — add_line is also a POST, with
        # its own narrower COUNCIL_ADMIN-only permission_classes on the
        # @action itself; branching on method here would silently override
        # that to this wider list instead of falling through to it.
        if self.action == "create":
            return [access_level_permission(AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT)()]
        return super().get_permissions()

    def get_serializer_class(self):
        return IssueBillSerializer if self.request.method == "POST" else BillSerializer

    def get_queryset(self):
        qs = Bill.objects.filter(council_id=self.request.user.council_id).order_by("-created_at")
        qs = qs.select_related("payer", "payer__enumerated_by", "payer__enumerated_by__consultant")
        qs = portfolio_filter(qs, self.request)
        params = self.request.query_params
        # Layered on top of portfolio_filter above, never instead of it — see
        # apply_payer_dimension_filters' docstring.
        qs = apply_payer_dimension_filters(qs, params, payer_path="payer")
        qs = apply_date_range(qs, params, field="created_at")  # created_at is the issue date

        status_param = params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        # Previously unparsed — `?payer=abc` raised a bare ValueError from the
        # ORM, which isn't a DRF APIException and so 500'd instead of 400ing.
        payer_param = parse_int(params, "payer")
        if payer_param is not None:
            qs = qs.filter(payer_id=payer_param)

        revenue_item_id = parse_int(params, "revenue_item_id")
        if revenue_item_id is not None:
            # Same join the /reports endpoint uses. .distinct() because a bill can
            # legitimately carry two lines for the same revenue item under
            # different rate bands (add_bill_line merges only when item+band+tier
            # all match), and the join would otherwise return that bill twice.
            qs = qs.filter(lines__assessment__council_revenue_item_id=revenue_item_id).distinct()

        value_min = parse_decimal(params, "value_min")
        if value_min is not None:
            qs = qs.filter(total_amount__gte=value_min)
        value_max = parse_decimal(params, "value_max")
        if value_max is not None:
            qs = qs.filter(total_amount__lte=value_max)

        q = params.get("q")
        if q:
            qs = qs.filter(models.Q(bill_ref__icontains=q) | models.Q(payer__full_name__icontains=q))
        return qs

    def create(self, request, *args, **kwargs):
        if request.user.access_level not in (AppRole.COUNCIL_ADMIN, AppRole.CONSULTANT, AppRole.AGENT):
            return Response({"error": "Not permitted"}, status=status.HTTP_403_FORBIDDEN)

        serializer = IssueBillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        payer = get_object_or_404(Payer, pk=data["payer_id"], council_id=request.user.council_id)
        lines = []
        for entry in data.get("lines", []):
            item = get_object_or_404(CouncilRevenueItem, pk=entry["revenue_item_id"], council_id=request.user.council_id)
            rate_band, rate_tier = _resolve_band_and_tier(entry, item)
            lines.append({
                "council_revenue_item": item,
                "quantity": entry["quantity"],
                "rate_band": rate_band,
                "rate_tier": rate_tier,
                "amount_override": entry.get("amount_override"),
            })

        try:
            bill = issue_bill(
                council_id=request.user.council_id,
                payer=payer,
                due_date=data.get("due_date"),
                lines=lines,
                bill_all_drafts=data["bill_all_drafts"],
                roll_arrears=data["roll_arrears"],
                actor=request.user,
            )
        except BillingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        payload = BillSerializer(bill).data
        payload["arrears_amount"] = bill.arrears_amount
        payload["superseded_count"] = getattr(bill, "superseded_count", 0)
        return Response(payload, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        bill = self.get_object()
        if Payment.objects.filter(bill=bill).exists():
            return Response(
                {"error": f"{bill.bill_ref} has payments on record and can't be deleted — this preserves the payment/receipt history."},
                status=status.HTTP_409_CONFLICT,
            )
        # The bill was a mistake in its entirety (wrong payer, wrong items,
        # duplicate) — its assessments go with it rather than lingering as
        # orphaned BILLED-status rows with no bill to show for them. This is
        # "undo an issued bill", not "waive/cancel a valid debt" — a payer
        # whose liability still stands should be re-billed, not have this
        # used as a write-off.
        assessment_ids = list(bill.lines.values_list("assessment_id", flat=True))
        audit(
            council_id=bill.council_id, actor=request.user, action="BILL_DELETED", entity_type="BILL",
            entity_id=bill.id, detail={"bill_ref": bill.bill_ref, "total_amount": str(bill.total_amount)},
        )
        response = super().destroy(request, *args, **kwargs)
        Assessment.objects.filter(id__in=assessment_ids).delete()
        return response

    @extend_schema(responses=BillDetailSerializer)
    @action(detail=True, methods=["get"], url_path="detail")
    def bill_detail(self, request, pk=None):
        bill = self.get_object()
        bill = Bill.objects.prefetch_related(
            "lines__assessment__council_revenue_item", "lines__assessment__rate_band", "lines__assessment__rate_tier",
            "supersedes__lines__assessment__council_revenue_item",
            "supersedes__lines__assessment__rate_band", "supersedes__lines__assessment__rate_tier",
        ).get(pk=bill.pk)
        return Response(BillDetailSerializer(bill).data)

    @extend_schema(request=AddLineSerializer, responses=BillLineDetailSerializer)
    @action(detail=True, methods=["post"], url_path="lines", permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)])
    def add_line(self, request, pk=None):
        bill = self.get_object()
        serializer = AddLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = get_object_or_404(CouncilRevenueItem, pk=serializer.validated_data["revenue_item_id"], council_id=request.user.council_id)
        rate_band, rate_tier = _resolve_band_and_tier(serializer.validated_data, item)
        try:
            line = add_bill_line(
                bill=bill,
                council_revenue_item=item,
                quantity=serializer.validated_data["quantity"],
                actor=request.user,
                rate_band=rate_band,
                rate_tier=rate_tier,
                amount_override=serializer.validated_data.get("amount_override"),
            )
        except BillingError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(BillLineDetailSerializer(line).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        methods=["PUT"], parameters=[OpenApiParameter("line_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=UpdateLineSerializer, responses=BillLineDetailSerializer,
    )
    @extend_schema(
        methods=["DELETE"], parameters=[OpenApiParameter("line_id", OpenApiTypes.INT, OpenApiParameter.PATH)],
        request=None, responses={204: None},
    )
    @action(
        detail=True, methods=["put", "delete"], url_path=r"lines/(?P<line_id>[0-9]+)",
        permission_classes=[access_level_permission(AppRole.COUNCIL_ADMIN)],
    )
    def line_detail(self, request, pk=None, line_id=None):
        bill = self.get_object()
        line = get_object_or_404(BillLine, pk=line_id, bill=bill)

        if request.method == "DELETE":
            try:
                delete_bill_line(line=line, actor=request.user)
            except BillingError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = UpdateLineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        line = update_bill_line(line=line, line_amount=serializer.validated_data["line_amount"], actor=request.user)
        return Response(BillLineDetailSerializer(line).data)


class PublicBillLookupView(APIView):
    """GET /api/v1/bills/<bill_ref> — public. Powers the demand-notice/demand-bill
    print pages and USSD option 1/2. Not part of BillViewSet's pk-based routing
    because bill_ref itself contains slashes (KAC/2026/000123)."""

    permission_classes = [AllowAny]

    @extend_schema(responses={200: PublicBillLookupSerializer, 404: None}, tags=["billing"])
    def get(self, request, bill_ref):
        from apps.tenancy.context import resolve_council_from_bill_ref

        council = resolve_council_from_bill_ref(bill_ref)
        if council is None:
            return Response({"error": "Bill not found"}, status=status.HTTP_404_NOT_FOUND)

        with council_context(council.id):
            bill = (
                Bill.objects.select_related("payer", "payer__ward")
                .prefetch_related(
                    "supersedes__lines__assessment__council_revenue_item",
                    "supersedes__lines__assessment__rate_band", "supersedes__lines__assessment__rate_tier",
                )
                .filter(bill_ref=bill_ref).first()
            )
            if bill is None:
                return Response({"error": "Bill not found"}, status=status.HTTP_404_NOT_FOUND)

            lines = bill.lines.select_related("assessment__council_revenue_item")
            return Response({
                "bill_ref": bill.bill_ref,
                "status": bill.status,
                "due_date": bill.due_date,
                "total_amount": bill.total_amount,
                "amount_paid": bill.amount_paid,
                "balance": bill.balance,
                "arrears_amount": bill.arrears_amount,
                "payer_ref": bill.payer.payer_ref,
                "full_name": bill.payer.full_name,
                "phone": bill.payer.phone,
                "address": bill.payer.address,
                "ward_name": bill.payer.ward.ward_name,
                "lines": BillLineDetailSerializer(lines, many=True).data,
                "superseded_bills": SupersededBillSerializer(bill.supersedes.all(), many=True).data,
            })
