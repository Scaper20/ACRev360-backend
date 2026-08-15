"""
Council onboarding as a first-class flow — V2_ARCHITECTURE.md §4.3/§10: "a fresh
council can be created, configured, and billed end-to-end."
"""
import datetime

import pytest
from django.db import transaction

from apps.accounts.models import AppRole, AppUser
from apps.billing.models import Bill
from apps.billing.services import issue_bill
from apps.payments.models import PaymentChannel
from apps.payments.services import post_payment
from apps.registry.models import Payer
from apps.revenue.models import RevenueCategory, RevenueItemTemplate
from apps.tenancy.context import set_council_context
from apps.tenancy.services import activate_template_item, onboard_council


@pytest.mark.django_db(transaction=True)
def test_fresh_council_onboards_configures_and_bills_end_to_end():
    # Create -> configure (bank accounts/prefix/print identity)
    council = onboard_council(
        council_code="NEW",
        council_name="New Test Area Council",
        config={
            "bill_ref_prefix": "NEW",
            "bill_due_days": 21,
            "revenue_bank_name": "Test Bank",
            "revenue_bank_account_number": "0000000000",
            "revenue_bank_account_name": "New Council Revenue",
        },
        actor=None,
    )
    assert council.config.bill_ref_prefix == "NEW"
    assert council.config.bill_due_days == 21

    with transaction.atomic():
        set_council_context(council.id)

        # Activate a template item at this council's own rate.
        category, _ = RevenueCategory.objects.get_or_create(name="Fees and Charges")
        template, _ = RevenueItemTemplate.objects.get_or_create(
            harmonised_code="ONB001", defaults={"item_name": "Onboarding Test Item", "unit_of_charge": "Per Annum", "category": category}
        )
        item = activate_template_item(council=council, template=template, rate_amount=12345, actor=None)
        assert item.current_rate.rate_amount == 12345

        # Create a council admin user.
        role, _ = AppRole.objects.get_or_create(name="ONB_ADMIN", defaults={"access_level": AppRole.COUNCIL_ADMIN})
        admin = AppUser.objects.create_user(
            username="onb-admin", password="testpass12345", full_name="Onboarding Admin", council=council, role=role,
        )

        # Onboarding as a ward.
        from apps.tenancy.models import WardZone

        ward = WardZone.objects.create(council=council, ward_code="ONBW1", ward_name="Onboarding Ward")

        # Enumerate a payer and bill them.
        payer = Payer.objects.create(
            council=council, payer_ref="C-0000001", payer_type=Payer.BUSINESS,
            full_name="Onboarding Test Payer", ward=ward, enumerated_by=admin,
        )
        bill = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item, "quantity": 1}], actor=admin)
        assert bill.bill_ref.startswith("NEW/")
        assert bill.total_amount == 12345
        assert bill.status == Bill.ISSUED
        assert bill.due_date == datetime.date.today() + datetime.timedelta(days=21)

        # Collect against it.
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        payment = post_payment(council_id=council.id, bill=bill, channel=channel, amount=12345, posted_by=admin)
        bill.refresh_from_db()
        assert bill.status == Bill.PAID
        assert payment.receipt.receipt_ref
