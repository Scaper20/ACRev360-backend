from rest_framework import serializers

from apps.tenancy.models import Council, CouncilConfig, Department, WardZone


class WardZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = WardZone
        fields = ["id", "ward_code", "ward_name", "zone_type"]
        read_only_fields = ["id"]


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "department_name", "department_code", "head_name", "head_phone"]
        read_only_fields = ["id"]


class CouncilConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CouncilConfig
        fields = [
            "bill_ref_prefix", "bill_due_days", "revenue_bank_name", "revenue_bank_account_number",
            "revenue_bank_account_name", "treasurer_name", "treasurer_phone",
            "print_signatory_name", "print_signatory_title",
        ]


class CouncilSerializer(serializers.ModelSerializer):
    config = CouncilConfigSerializer(read_only=True)

    class Meta:
        model = Council
        fields = ["id", "council_code", "council_name", "is_active", "config"]
        read_only_fields = ["id", "is_active", "config"]


class OnboardCouncilSerializer(serializers.Serializer):
    council_code = serializers.CharField(max_length=16)
    council_name = serializers.CharField(max_length=120)
    config = CouncilConfigSerializer()
