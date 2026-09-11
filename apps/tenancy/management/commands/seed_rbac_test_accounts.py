"""
Creates dedicated QA/RLS-boundary test logins for every access level added in
the RBAC expansion (docs/RBAC_EXPANSION_DESIGN.md), per
docs/acrev360-roles-permissions-matrix.md's "Test Credentials Requirement":
two accounts per council-scoped role (in two different scopes, so a leak
across councils/firms/wards/payers is actually testable), one per
platform-tier role (council is null — there's only one platform).

Deliberately separate from seed_demo_data/seed_starter_data: those build a
believable single-council demo dataset; this builds the minimum scaffolding
to prove RLS/permission boundaries hold, and is meant to be run once against
a QA environment, not bundled into the regular demo seed.

Requires seed_kuje to have already run (needs at least one council with an
active COUNCIL_ADMIN, at least one ward, and at least one active consultant —
run seed_starter_data too if the council has neither yet). If only one
council exists, the "two councils" cases (platform EXTERNAL_AUDITOR's grant
scoping, and generally proving cross-council isolation) fall back to
onboarding a second bare council here so the leakage check is real rather
than skipped.
"""
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AppRole, AppUser, CouncilGrant, FieldAgent, SubConsultant
from apps.registry.models import Payer, PayerDelegation
from apps.registry.services import create_payer
from apps.tenancy.context import set_council_context
from apps.tenancy.models import Council, WardZone
from apps.tenancy.services import onboard_council

# (named_role, access_level) — extends seed_kuje.py's ROLES registry with
# every level added by the RBAC expansion. get_or_create'd here rather than
# assuming seed_kuje already made them, since seed_kuje's own ROLES list is
# deliberately just KAC's core five.
NAMED_ROLES = [
    ("SUPER_ADMIN", AppRole.SUPER_ADMIN),
    ("PLATFORM_ADMIN", AppRole.PLATFORM_ADMIN),
    ("DEVOPS_ADMIN", AppRole.DEVOPS_ADMIN),
    ("BD_ACCOUNT_MANAGER", AppRole.BD_VIEW),
    ("COMPLIANCE_OFFICER", AppRole.COMPLIANCE_VIEW),
    ("FINANCE_BILLING_ADMIN", AppRole.FINANCE_ADMIN),
    ("SUPPORT_HELPDESK", AppRole.SUPPORT_ADMIN),
    ("DATA_ANALYST", AppRole.ANALYTICS_VIEW),
    ("EXTERNAL_AUDITOR", AppRole.EXTERNAL_AUDITOR),
    ("COUNCIL_IGR_HEAD", AppRole.COUNCIL_IGR_HEAD),
    ("COUNCIL_TREASURY_OFFICER", AppRole.COUNCIL_TREASURY),
    ("COUNCIL_INTERNAL_AUDITOR", AppRole.COUNCIL_AUDITOR),
    ("COUNCIL_IT_OFFICER", AppRole.COUNCIL_IT),
    ("CONSULTANT_FIELD_STAFF", AppRole.CONSULTANT_STAFF),
    ("AGENT_SUPERVISOR", AppRole.AGENT_SUPERVISOR),
    ("RATEPAYER", AppRole.RATEPAYER),
    ("RATEPAYER_PROXY", AppRole.RATEPAYER_PROXY),
]

#: Roles that need a real council to belong to, one account per council in
#: `_council_ids` below. Platform tier isn't here — those get exactly one
#: account each, council=null.
_COUNCIL_TIER_ROLES = [
    "COUNCIL_IGR_HEAD", "COUNCIL_TREASURY_OFFICER", "COUNCIL_INTERNAL_AUDITOR", "COUNCIL_IT_OFFICER",
]
_PLATFORM_TIER_ROLES = [
    "SUPER_ADMIN", "PLATFORM_ADMIN", "DEVOPS_ADMIN", "BD_ACCOUNT_MANAGER", "COMPLIANCE_OFFICER",
    "FINANCE_BILLING_ADMIN", "SUPPORT_HELPDESK", "DATA_ANALYST",
]


class Command(BaseCommand):
    help = "Seed two-scope QA test logins for every RBAC-expansion access level (see docs/RBAC_EXPANSION_DESIGN.md)."

    @transaction.atomic
    def handle(self, *args, **options):
        councils = list(Council.objects.filter(is_active=True).order_by("id"))
        if not councils:
            raise CommandError("No active council found — run seed_kuje first.")
        if len(councils) == 1:
            self.stdout.write(self.style.WARNING(
                "Only one active council found — onboarding a second bare one (RBACQA) "
                "so cross-council leakage is actually testable, not just skipped."
            ))
            councils.append(onboard_council(
                council_code="RBACQA", council_name="RBAC QA Second Council",
                config={"bill_ref_prefix": "RQA", "bill_due_days": 30}, actor=None,
            ))
        council_a, council_b = councils[0], councils[1]

        role_map = {}
        for name, access_level in NAMED_ROLES:
            role, _ = AppRole.objects.get_or_create(name=name, defaults={"access_level": access_level})
            role_map[name] = role

        password = secrets.token_urlsafe(12)
        created_usernames = []

        def make_user(username, role_name, *, council=None, consultant=None):
            if AppUser.objects.filter(username=username).exists():
                self.stdout.write(self.style.WARNING(f"  {username} already exists — skipping"))
                return AppUser.objects.get(username=username)
            user = AppUser.objects.create_user(
                username=username, password=password, full_name=f"RBAC QA — {role_name}",
                council=council, role=role_map[role_name], consultant=consultant,
            )
            created_usernames.append(username)
            return user

        # --- Platform tier: one account each, council=null ---
        for role_name in _PLATFORM_TIER_ROLES:
            make_user(f"qa_{role_name.lower()}", role_name)

        # EXTERNAL_AUDITOR: platform tier too, but scoped via a time-boxed
        # CouncilGrant instead of seeing every council — one active grant
        # (council_a) and one already-expired grant (council_b), so both
        # "in scope" and "expired, no longer in scope" are directly testable.
        auditor = make_user("qa_external_auditor", "EXTERNAL_AUDITOR")
        CouncilGrant.objects.get_or_create(user=auditor, council=council_a, defaults={"expires_at": None})
        CouncilGrant.objects.get_or_create(
            user=auditor, council=council_b, defaults={"expires_at": timezone.now() - timezone.timedelta(days=1)}
        )

        # --- Council tier: two accounts, one per council ---
        for role_name in _COUNCIL_TIER_ROLES:
            for suffix, council in (("a", council_a), ("b", council_b)):
                make_user(f"qa_{role_name.lower()}_{suffix}", role_name, council=council)

        # --- Consultant/agent tier: needs a real firm/ward per council ---
        for suffix, council in (("a", council_a), ("b", council_b)):
            set_council_context(council.id)
            consultant = SubConsultant.objects.filter(council=council, status=SubConsultant.ACTIVE).first()
            if consultant is None:
                self.stdout.write(self.style.WARNING(
                    f"  {council.council_code} has no active consultant — run seed_starter_data first; "
                    "skipping CONSULTANT_FIELD_STAFF/AGENT_SUPERVISOR for this council."
                ))
                continue
            make_user(f"qa_consultant_field_staff_{suffix}", "CONSULTANT_FIELD_STAFF", council=council, consultant=consultant)

            ward = WardZone.objects.filter(council=council).first()
            supervisor_user = make_user(f"qa_agent_supervisor_{suffix}", "AGENT_SUPERVISOR", council=council, consultant=consultant)
            if ward is not None and not FieldAgent.objects.filter(user=supervisor_user).exists():
                FieldAgent.objects.create(
                    council=council, user=supervisor_user, agent_code=f"SUP-{suffix.upper()}-00001",
                    assigned_ward=ward, status=FieldAgent.ACTIVE,
                )

        # --- Ratepayer tier: two payers, one proxy delegated to only one of
        # them, so "own account" vs "delegated" vs "neither" are all
        # distinguishable in a leakage test. ---
        set_council_context(council_a.id)
        ward = WardZone.objects.filter(council=council_a).first()
        if ward is not None:
            admin = AppUser.objects.filter(council=council_a, role__access_level=AppRole.COUNCIL_ADMIN).first()
            payer_a = Payer.objects.filter(council=council_a, payer_ref__startswith="IND-", user__isnull=False).first()
            if payer_a is None and admin is not None:
                payer_a, _ = create_payer(
                    council_id=council_a.id, actor=admin, payer_type=Payer.INDIVIDUAL,
                    first_name="RBAC", last_name="QA Ratepayer A", ward=ward, phone="08000000101",
                )
            payer_b, _ = (None, None)
            if admin is not None:
                payer_b, _ = create_payer(
                    council_id=council_a.id, actor=admin, payer_type=Payer.INDIVIDUAL,
                    first_name="RBAC", last_name="QA Ratepayer B", ward=ward, phone="08000000102", force=True,
                )

            if payer_a is not None and payer_a.user_id is None:
                payer_a.user = make_user("qa_ratepayer_a", "RATEPAYER", council=council_a)
                payer_a.save(update_fields=["user"])
            if payer_b is not None and payer_b.user_id is None:
                payer_b.user = make_user("qa_ratepayer_b", "RATEPAYER", council=council_a)
                payer_b.save(update_fields=["user"])

            proxy = make_user("qa_ratepayer_proxy", "RATEPAYER_PROXY", council=council_a)
            if payer_a is not None:
                PayerDelegation.objects.get_or_create(
                    council=council_a, payer=payer_a, proxy_user=proxy,
                    defaults={"granted_by": admin or proxy},
                )
                self.stdout.write(f"  qa_ratepayer_proxy delegated to {payer_a.payer_ref} only — {payer_b.payer_ref if payer_b else 'payer B'} must stay invisible to it.")
        else:
            self.stdout.write(self.style.WARNING(f"  {council_a.council_code} has no ward — skipping ratepayer tier."))

        self.stdout.write(self.style.SUCCESS(f"\nCreated {len(created_usernames)} new account(s). Shared password: {password}"))
        self.stdout.write(self.style.WARNING("Store this password now — it is not shown again."))
        for username in created_usernames:
            self.stdout.write(f"  {username}")
