from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

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
    ACCESS_LEVEL_CHOICES = [
        (COUNCIL_ADMIN, "Council Admin"),
        (CONSULTANT, "Consultant"),
        (AGENT, "Agent"),
        (GLOBAL_VIEW, "Global View"),
    ]

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
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    council = models.ForeignKey(Council, on_delete=models.PROTECT, null=True, blank=True, related_name="users")
    role = models.ForeignKey(AppRole, on_delete=models.PROTECT, null=True, blank=True, related_name="users")
    consultant = models.ForeignKey(
        "accounts.SubConsultant", on_delete=models.PROTECT, null=True, blank=True, related_name="users"
    )

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
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

    class Meta:
        db_table = "council_grant"
        constraints = [
            models.UniqueConstraint(fields=["user", "council"], name="uniq_council_grant"),
        ]


class SubConsultant(CouncilScopedModel):
    PENDING, ACTIVE, SUSPENDED, EXITED = "PENDING", "ACTIVE", "SUSPENDED", "EXITED"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (EXITED, "Exited"),
    ]

    consultant_name = models.CharField(max_length=160)
    contract_ref = models.CharField(max_length=64)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, help_text="Percent, e.g. 30.00")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)

    class Meta:
        db_table = "sub_consultant"
        constraints = [
            models.UniqueConstraint(fields=["council", "contract_ref"], name="uniq_contract_ref_per_council"),
        ]
        ordering = ["consultant_name"]

    def __str__(self):
        return self.consultant_name


class FieldAgent(CouncilScopedModel):
    ACTIVE, SUSPENDED, EXITED = "ACTIVE", "SUSPENDED", "EXITED"
    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (EXITED, "Exited"),
    ]

    user = models.OneToOneField(AppUser, on_delete=models.CASCADE, related_name="field_agent")
    agent_code = models.CharField(max_length=32)
    assigned_ward = models.ForeignKey(WardZone, on_delete=models.PROTECT, null=True, blank=True, related_name="agents")
    device_imei = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE)

    class Meta:
        db_table = "field_agent"
        constraints = [
            models.UniqueConstraint(fields=["council", "agent_code"], name="uniq_agent_code_per_council"),
        ]

    def __str__(self):
        return self.agent_code
