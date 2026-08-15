from rest_framework import serializers

from apps.accounts.models import AppUser, FieldAgent, SubConsultant
from apps.revenue.models import ConsultantPortfolio


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class MeSerializer(serializers.ModelSerializer):
    access_level = serializers.CharField(read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    council_code = serializers.CharField(source="council.council_code", read_only=True, default=None)

    class Meta:
        model = AppUser
        fields = [
            "id", "username", "full_name", "email", "phone",
            "council", "council_code", "role", "role_name", "consultant", "access_level",
        ]
        read_only_fields = fields


class SubConsultantSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubConsultant
        fields = ["id", "consultant_name", "contract_ref", "commission_rate", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


class SubConsultantStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SubConsultant.STATUS_CHOICES)


class FieldAgentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(write_only=True)
    username = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, required=False)
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)
    consultant_id = serializers.IntegerField(read_only=True, source="user.consultant_id")

    class Meta:
        model = FieldAgent
        fields = [
            "id", "agent_code", "assigned_ward", "device_imei", "status",
            "full_name", "username", "password", "phone", "consultant_id",
        ]
        read_only_fields = ["id", "agent_code", "status", "consultant_id"]


class ConsultantPortfolioSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultantPortfolio
        fields = ["id", "consultant", "council_revenue_item", "ward", "effective_from", "effective_to"]
        read_only_fields = ["id", "effective_from", "effective_to"]
