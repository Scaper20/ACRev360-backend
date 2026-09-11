from rest_framework import serializers

from apps.revenue.models import CouncilRevenueItem, RateBand, RateTier, RevenueCategory, RevenueItemTemplate


class RevenueCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = RevenueCategory
        fields = ["id", "name", "sort_order"]


class RevenueItemTemplateSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = RevenueItemTemplate
        fields = ["id", "harmonised_code", "item_name", "category", "category_name", "unit_of_charge", "in_initial_scope"]


class RateTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = RateTier
        fields = ["id", "label", "amount", "sort_order"]
        read_only_fields = fields


class RateBandSerializer(serializers.ModelSerializer):
    tiers = RateTierSerializer(many=True, read_only=True)

    class Meta:
        model = RateBand
        fields = [
            "id", "label", "sort_order", "rate_mode", "flat_amount", "min_amount", "max_amount", "tiers",
        ]
        read_only_fields = fields


class CouncilRevenueItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    current_rate = serializers.DecimalField(source="current_rate.rate_amount", max_digits=14, decimal_places=2, read_only=True, default=None)
    rate_id = serializers.IntegerField(source="current_rate.id", read_only=True, default=None)
    rate_bands = RateBandSerializer(source="active_bands", many=True, read_only=True)
    department_name = serializers.CharField(source="department.department_name", read_only=True, default=None)

    class Meta:
        model = CouncilRevenueItem
        fields = [
            "id", "template", "harmonised_code", "item_name", "category", "category_name",
            "unit_of_charge", "is_active", "current_rate", "rate_id", "rate_bands",
            "department", "department_name",
        ]
        # department is set only via the dedicated `department` action (see
        # CouncilRevenueItemViewSet) — read-only here, same reasoning as
        # current_rate/rate_id above.
        read_only_fields = ["id", "current_rate", "rate_id", "rate_bands", "department", "department_name"]


class ChangeRateSerializer(serializers.Serializer):
    rate_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)


class SetDepartmentSerializer(serializers.Serializer):
    department_id = serializers.IntegerField(allow_null=True)


class RateTierEntrySerializer(serializers.Serializer):
    label = serializers.CharField(max_length=40)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)


class RateBandEntrySerializer(serializers.Serializer):
    """One band in a `POST .../rate-bands` replacement set. `label` may be blank
    only when this is the item's single band (no gazetted sub-classification).
    Which of flat_amount / min_amount+max_amount / tiers is required depends on
    rate_mode — validated in apps.revenue.services._validate_band_spec, not here,
    so the error message can name the offending band by label."""

    label = serializers.CharField(max_length=160, allow_blank=True, default="")
    rate_mode = serializers.ChoiceField(choices=[RateBand.FLAT, RateBand.RANGE, RateBand.TIERED])
    flat_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True, default=None)
    min_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True, default=None)
    max_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0, required=False, allow_null=True, default=None)
    tiers = RateTierEntrySerializer(many=True, required=False, default=list)


class ReplaceRateBandsSerializer(serializers.Serializer):
    bands = RateBandEntrySerializer(many=True, default=list)
