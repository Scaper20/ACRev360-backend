from rest_framework import serializers

from apps.accounts.models import AppUser, FieldAgent, SubConsultant
from apps.revenue.models import AgentPortfolio, ConsultantPortfolio


class LogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class MeSerializer(serializers.ModelSerializer):
    access_level = serializers.CharField(read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default=None)
    council_code = serializers.CharField(source="council.council_code", read_only=True, default=None)
    # Denormalized rather than a second /consultants/{id} call — a
    # CONSULTANT-role user can't list SubConsultantViewSet at all (see its
    # get_permissions()), so this is the only way their own dashboard can
    # show who they are without a dedicated "my consultant" endpoint.
    consultant_name = serializers.CharField(source="consultant.consultant_name", read_only=True, default=None)
    consultant_commission_rate = serializers.DecimalField(source="consultant.commission_rate", max_digits=5, decimal_places=2, read_only=True, default=None)
    consultant_status = serializers.CharField(source="consultant.status", read_only=True, default=None)

    class Meta:
        model = AppUser
        fields = [
            "id", "username", "full_name", "email", "phone",
            "council", "council_code", "role", "role_name", "consultant", "access_level",
            "consultant_name", "consultant_commission_rate", "consultant_status",
        ]
        read_only_fields = fields


class SubConsultantSerializer(serializers.ModelSerializer):
    # Optional — onboarding a firm with no login yet is still valid (matches
    # FieldAgentSerializer's shape, but here the login is for one manager of
    # the firm, not the firm itself, hence the manager_ prefix).
    manager_username = serializers.CharField(write_only=True, required=False)
    manager_password = serializers.CharField(write_only=True, required=False)
    manager_full_name = serializers.CharField(write_only=True, required=False)
    has_login = serializers.SerializerMethodField()

    class Meta:
        model = SubConsultant
        fields = [
            "id", "consultant_name", "contract_ref", "commission_rate", "status", "created_at",
            "manager_username", "manager_password", "manager_full_name", "has_login",
        ]
        read_only_fields = ["id", "status", "created_at", "has_login"]

    def get_has_login(self, obj):
        return obj.users.exists()

    def validate(self, attrs):
        if attrs.get("manager_username") and not attrs.get("manager_full_name"):
            raise serializers.ValidationError({"manager_full_name": "Required when manager_username is given."})
        return attrs


class SubConsultantStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=SubConsultant.STATUS_CHOICES)


class StakeholderSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField()
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = AppUser
        fields = ["id", "username", "full_name", "phone", "password", "is_active", "date_joined"]
        read_only_fields = ["id", "is_active", "date_joined"]


class FieldAgentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(write_only=True)
    username = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, required=False)
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)
    consultant_id = serializers.IntegerField(read_only=True, source="user.consultant_id")
    # Separate from the write-only `full_name`/`phone` above (those set the
    # linked AppUser at creation and are popped from validated_data by
    # perform_create) — these are the read side, for display/search.
    agent_full_name = serializers.CharField(read_only=True, source="user.full_name")
    agent_phone = serializers.CharField(read_only=True, source="user.phone")

    class Meta:
        model = FieldAgent
        fields = [
            "id", "agent_code", "assigned_ward", "device_imei", "status",
            "full_name", "username", "password", "phone", "consultant_id", "agent_full_name", "agent_phone",
        ]
        read_only_fields = ["id", "agent_code", "status", "consultant_id", "agent_full_name", "agent_phone"]


class ConsultantPortfolioSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsultantPortfolio
        fields = ["id", "consultant", "council_revenue_item", "ward", "effective_from", "effective_to"]
        # consultant is set by the view from the URL (SubConsultantViewSet.portfolio),
        # not client input — read-only here, same reasoning as AgentPortfolioSerializer's
        # own `agent` field below. Was previously required input with no way to
        # satisfy it (the view never puts it in request.data), so every assignment
        # 400'd before ever reaching serializer.save() — pre-existing bug, caught
        # while adding the same field to AgentPortfolioSerializer.
        read_only_fields = ["id", "consultant", "effective_from", "effective_to"]


class AgentPortfolioSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentPortfolio
        fields = ["id", "agent", "council_revenue_item", "ward", "effective_from", "effective_to"]
        # agent is set by the view from the URL (FieldAgentViewSet.portfolio),
        # not client input.
        read_only_fields = ["id", "agent", "effective_from", "effective_to"]
