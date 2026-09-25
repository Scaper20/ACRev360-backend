from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.accounts.managers import AppUserManager
from apps.common.models import TimeStampedModel
from apps.tenancy.models import Council, CouncilScopedModel, WardZone


class AppRole(models.Model):
    """
    Council-agnostic role definitions. Several named roles can map to the same
    access level (e.g. both `COUNCIL_ADMIN` and `HEAD_REVENUE` carry the
    `COUNCIL_ADMIN` access level) — see SCHEMA.md §2.
    """

    COUNCIL_ADMIN = "COUNCIL_ADMIN"
    CONSULTANT = "CONSULTANT"
    AGENT = "AGENT"
    GLOBAL_VIEW = "GLOBAL_VIEW"
    REVENUE_OFFICER = "REVENUE_OFFICER"
    # ACDSL (platform) tier — council is null on these AppUsers. See
    # docs/RBAC_EXPANSION_DESIGN.md for the full mapping from the roles
    # matrix draft onto these buckets, and apps/common/platform_scope.py
    # for how cross-council read access is granted without an RLS bypass.
    SUPER_ADMIN = "SUPER_ADMIN"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    DEVOPS_ADMIN = "DEVOPS_ADMIN"
    BD_VIEW = "BD_VIEW"
    COMPLIANCE_VIEW = "COMPLIANCE_VIEW"
    FINANCE_ADMIN = "FINANCE_ADMIN"
    SUPPORT_ADMIN = "SUPPORT_ADMIN"
    ANALYTICS_VIEW = "ANALYTICS_VIEW"
    EXTERNAL_AUDITOR = "EXTERNAL_AUDITOR"
    # Council tier additions
    COUNCIL_IGR_HEAD = "COUNCIL_IGR_HEAD"
    COUNCIL_TREASURY = "COUNCIL_TREASURY"
    COUNCIL_AUDITOR = "COUNCIL_AUDITOR"
    COUNCIL_IT = "COUNCIL_IT"
    # Consultant tier addition
    CONSULTANT_STAFF = "CONSULTANT_STAFF"
    # Field agent tier addition
    AGENT_SUPERVISOR = "AGENT_SUPERVISOR"
    # Ratepayer tier — self-service, never granted a staff-facing
    # permission; see apps/registry/api and the closed-world test.
    RATEPAYER = "RATEPAYER"
    RATEPAYER_PROXY = "RATEPAYER_PROXY"
    ACCESS_LEVEL_CHOICES = [
        (COUNCIL_ADMIN, "Council Admin"),
        (CONSULTANT, "Consultant"),
        (AGENT, "Agent"),
        (GLOBAL_VIEW, "Global View"),
        (REVENUE_OFFICER, "Revenue Officer"),
        (SUPER_ADMIN, "Super Admin"),
        (PLATFORM_ADMIN, "Platform Admin"),
        (DEVOPS_ADMIN, "DevOps Admin"),
        (BD_VIEW, "BD / Account Manager"),
        (COMPLIANCE_VIEW, "Compliance View"),
        (FINANCE_ADMIN, "Finance Admin (ACDSL)"),
        (SUPPORT_ADMIN, "Support Admin"),
        (ANALYTICS_VIEW, "Analytics View"),
        (EXTERNAL_AUDITOR, "External Auditor"),
        (COUNCIL_IGR_HEAD, "Council IGR Head"),
        (COUNCIL_TREASURY, "Council Treasury"),
        (COUNCIL_AUDITOR, "Council Auditor"),
        (COUNCIL_IT, "Council IT"),
        (CONSULTANT_STAFF, "Consultant Staff"),
        (AGENT_SUPERVISOR, "Agent Supervisor"),
        (RATEPAYER, "Ratepayer"),
        (RATEPAYER_PROXY, "Ratepayer Proxy"),
    ]

    #: Access levels that always carry council=null (ACDSL/platform tier),
    #: for validation/seeding convenience — see AppUser.council's docstring.
    PLATFORM_TIER_LEVELS = (
        SUPER_ADMIN, PLATFORM_ADMIN, DEVOPS_ADMIN, BD_VIEW, COMPLIANCE_VIEW,
        FINANCE_ADMIN, SUPPORT_ADMIN, ANALYTICS_VIEW, EXTERNAL_AUDITOR,
    )

    name = models.CharField(max_length=64, unique=True)
    access_level = models.CharField(max_length=16, choices=ACCESS_LEVEL_CHOICES)

    class Meta:
        db_table = "app_role"

    def __str__(self):
        return self.name


class AppUser(AbstractBaseUser, PermissionsMixin):
    """
    Login identity. `council` is set for normal (single-council) staff/consultant/
    agent users and null for platform-level accounts (Django superusers, and future
    FCT-level oversight identities that instead hold explicit `CouncilGrant` rows —
    see V2_ARCHITECTURE.md §3/§8). `consultant` is set for consultant-side users
    (managers and their agents), null for Council-direct staff.
    """

    username = models.CharField(max_length=64, unique=True)
    full_name = models.CharField(max_length=160)
    #: The login identifier (see AppTokenObtainPairSerializer) — required and
    #: unique, though USERNAME_FIELD itself deliberately stays "username" to
    #: avoid touching Django admin/permissions internals that key off it too.
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)

    council = models.ForeignKey(Council, on_delete=models.PROTECT, null=True, blank=True, related_name="users")
    role = models.ForeignKey(AppRole, on_delete=models.PROTECT, null=True, blank=True, related_name="users")
    consultant = models.ForeignKey(
        "accounts.SubConsultant", on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    #: Set when the account was provisioned with a system-generated password
    #: that must be replaced before normal use (ENFORCE_ACCOUNT_PASSWORD_POLICY).
    #: Cleared by POST /api/v1/auth/change-password; a JWT carrying the flag is
    #: 428-gated by apps.tenancy.middleware to force the change on first login.
    must_change_password = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = AppUserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "app_user"

    def __str__(self):
        return self.username

    @property
    def access_level(self):
        return self.role.access_level if self.role_id else None


class CouncilGrant(TimeStampedModel):
    """
    Explicit multi-council read grant for FCT/oversight-level users (`council` is
    null on the user itself). Not exercised until a second council is onboarded —
    see V2_ARCHITECTURE.md §11 phase 5 — but modeled now per §3's explicit design:
    "FCT/oversight roles get an explicit multi-council context, not a policy bypass."
    """

    user = models.ForeignKey(AppUser, on_delete=models.CASCADE, related_name="council_grants")
    council = models.ForeignKey(Council, on_delete=models.CASCADE, related_name="oversight_grants")
    #: Null means the grant never expires. Set for EXTERNAL_AUDITOR-style
    #: time-boxed access (docs/acrev360-roles-permissions-matrix.md's
    #: "likely needs a temporary/expiring access grant") — enforced by
    #: apps.common.platform_scope.granted_council_ids, not by RLS itself.
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "council_grant"
        constraints = [
            models.UniqueConstraint(fields=["user", "council"], name="uniq_council_grant"),
        ]

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at < timezone.now())


class SubConsultant(CouncilScopedModel):
    PENDING, ACTIVE, SUSPENDED, EXITED = "PENDING", "ACTIVE", "SUSPENDED", "EXITED"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (EXITED, "Exited"),
    ]

    NIN, PASSPORT, DRIVERS_LICENSE, VOTERS_CARD = "NIN", "PASSPORT", "DRIVERS_LICENSE", "VOTERS_CARD"
    ID_TYPE_CHOICES = [
        (NIN, "NIN"),
        (PASSPORT, "International Passport"),
        (DRIVERS_LICENSE, "Driver's License"),
        (VOTERS_CARD, "Voter's Card"),
    ]

    consultant_name = models.CharField(max_length=160)
    contract_ref = models.CharField(max_length=64)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percent, e.g. 30.00")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)
    contract_start_date = models.DateField(null=True, blank=True)
    contract_end_date = models.DateField(null=True, blank=True, help_text="Blank for an open-ended contract.")
    # The firm's own billable identity — set at onboarding, when a registration
    # bill is auto-issued against it (see SubConsultantViewSet.perform_create).
    # Null only for consultants onboarded before this existed.
    registration_payer = models.OneToOneField(
        "registry.Payer", on_delete=models.PROTECT, null=True, blank=True, related_name="consultant"
    )
    # KYC — the firm's authorized signatory. authorized_signatory_id_hash follows
    # Payer.nin_bvn_hash's exact convention: a pre-hashed value handed in by the
    # caller, not hashed here — this field is a plain store, not a hasher.
    authorized_signatory_name = models.CharField(max_length=160, blank=True)
    authorized_signatory_id_type = models.CharField(max_length=32, choices=ID_TYPE_CHOICES, blank=True)
    authorized_signatory_id_hash = models.CharField(max_length=128, blank=True)
    registered_address = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "sub_consultant"
        constraints = [
            models.UniqueConstraint(fields=["council", "contract_ref"], name="uniq_contract_ref_per_council"),
        ]
        ordering = ["consultant_name"]

    def __str__(self):
        return self.consultant_name

    @property
    def is_contract_expired(self):
        return bool(self.contract_end_date and self.contract_end_date < timezone.localdate())


class FieldAgent(CouncilScopedModel):
    ACTIVE, SUSPENDED, EXITED = "ACTIVE", "SUSPENDED", "EXITED"
    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (EXITED, "Exited"),
    ]

    NIN, PASSPORT, DRIVERS_LICENSE, VOTERS_CARD = "NIN", "PASSPORT", "DRIVERS_LICENSE", "VOTERS_CARD"
    ID_TYPE_CHOICES = [
        (NIN, "NIN"),
        (PASSPORT, "International Passport"),
        (DRIVERS_LICENSE, "Driver's License"),
        (VOTERS_CARD, "Voter's Card"),
    ]

    user = models.OneToOneField(AppUser, on_delete=models.CASCADE, related_name="field_agent")
    agent_code = models.CharField(max_length=32)
    assigned_ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, null=True, blank=True, related_name="agents")
    device_imei = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE)
    # KYC — same hashed-value convention as Payer.nin_bvn_hash / SubConsultant.
    # authorized_signatory_id_hash above.
    id_type = models.CharField(max_length=32, choices=ID_TYPE_CHOICES, blank=True)
    id_hash = models.CharField(max_length=128, blank=True)
    next_of_kin_name = models.CharField(max_length=160, blank=True)
    next_of_kin_phone = models.CharField(max_length=32, blank=True)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "field_agent"
        constraints = [
            models.UniqueConstraint(fields=["council", "agent_code"], name="uniq_agent_code_per_council"),
        ]

    def __str__(self):
        return self.agent_code


class StakeholderProfile(models.Model):
    """Role-specific fields for a GLOBAL_VIEW stakeholder login. Kept off
    AppUser itself — every other account type (council-staff logins in
    particular) has no use for an address, so this stays a separate table
    joined only for stakeholders, the same way FieldAgent already keeps
    agent-only fields off AppUser. See StakeholderSerializer/StakeholderViewSet."""

    user = models.OneToOneField(AppUser, on_delete=models.CASCADE, related_name="stakeholder_profile")
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "stakeholder_profile"

    def __str__(self):
        return self.user.username


class RevenueOfficerProfile(models.Model):
    """Role-specific fields for a REVENUE_OFFICER login — same reasoning as
    StakeholderProfile (see its docstring)."""

    user = models.OneToOneField(AppUser, on_delete=models.CASCADE, related_name="revenue_officer_profile")
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "revenue_officer_profile"

    def __str__(self):
        return self.user.username
