from rest_framework import serializers

from apps.revenue.models import CouncilRevenueItem, RevenueCategory, RevenueItemTemplate


class RevenueCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = RevenueCategory
        fields = ["id", "name", "sort_order"]


class RevenueItemTemplateSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = RevenueItemTemplate
        fields = ["id", "harmonised_code", "item_name", "category", "category_name", "unit_of_charge", "in_initial_scope"]


class CouncilRevenueItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    current_rate = serializers.DecimalField(source="current_rate.rate_amount", max_digits=14, decimal_places=2, read_only=True, default=None)
    rate_id = serializers.IntegerField(source="current_rate.id", read_only=True, default=None)

    class Meta:
        model = CouncilRevenueItem
        fields = [
            "id", "template", "harmonised_code", "item_name", "category", "category_name",
            "unit_of_charge", "is_active", "current_rate", "rate_id",
        ]
        read_only_fields = ["id", "current_rate", "rate_id"]


class ChangeRateSerializer(serializers.Serializer):
    rate_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
