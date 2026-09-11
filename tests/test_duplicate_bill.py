"""
One active bill per payer per year — each revenue item is billed yearly, so a
second issue_bill() call for a payer who already has a non-terminal bill this
year should warn (409 + duplicate_of) rather than silently create a second
bill, matching the warn->confirm->force contract PayerFormModal.tsx already
uses for duplicate-payer detection. roll_arrears (consolidation) is exempt —
it's the sanctioned way to issue a bill while a prior one is open, and it
immediately supersedes that prior bill itself.
"""
import pytest
from django.db import transaction

from apps.audit.models import AuditLog
from apps.billing.models import Bill
from apps.billing.services import DuplicateBill, issue_bill
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_payer, make_revenue_item):
    council = make_council(code="DUP")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="dup-admin")
        payer = make_payer(council, ward, admin)
        item_a = make_revenue_item(council, code="DUPITEMA", rate=10000)
        item_b = make_revenue_item(council, code="DUPITEMB", rate=5000)
        yield {"council": council, "ward": ward, "admin": admin, "payer": payer, "item_a": item_a, "item_b": item_b}


@pytest.mark.django_db(transaction=True)
def test_second_bill_same_year_raises_duplicate(scoped):
    council, payer, admin, item_a, item_b = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item_a"], scoped["item_b"],
    )
    first = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)

    with pytest.raises(DuplicateBill) as exc_info:
        issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}], actor=admin)
    assert exc_info.value.existing.id == first.id


@pytest.mark.django_db(transaction=True)
def test_force_true_issues_second_bill_and_audits_override(scoped):
    council, payer, admin, item_a, item_b = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item_a"], scoped["item_b"],
    )
    first = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)

    second = issue_bill(
        council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}], actor=admin, force=True,
    )
    assert second.id != first.id
    entry = AuditLog.objects.get(action="BILL_ISSUED_FORCED_DUPLICATE", entity_id=str(second.id))
    assert entry.actor_id == admin.id
    assert entry.detail["bypassed_bill_id"] == first.id
    assert entry.detail["bypassed_bill_ref"] == first.bill_ref


@pytest.mark.django_db(transaction=True)
def test_cancelled_prior_bill_does_not_block_new_bill(scoped):
    council, payer, admin, item_a, item_b = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item_a"], scoped["item_b"],
    )
    first = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)
    first.status = Bill.CANCELLED
    first.save(update_fields=["status"])

    second = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_b, "quantity": 1}], actor=admin)
    assert second.id != first.id


@pytest.mark.django_db(transaction=True)
def test_roll_arrears_exempt_from_duplicate_check(scoped):
    """Consolidation (roll_arrears=True) is the sanctioned way to issue a bill
    while a prior one is still open — it must never be blocked by the
    duplicate-bill guard, since it immediately supersedes that prior bill."""
    council, payer, admin, item_a = scoped["council"], scoped["payer"], scoped["admin"], scoped["item_a"]
    first = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)

    consolidated = issue_bill(council_id=council.id, payer=payer, roll_arrears=True, actor=admin)
    assert consolidated.id != first.id
    first.refresh_from_db()
    assert first.status == Bill.SUPERSEDED


@pytest.mark.django_db(transaction=True)
def test_duplicate_bill_returns_409_with_reference_via_api(scoped, authed_api_client):
    council, payer, admin, item_a, item_b = (
        scoped["council"], scoped["payer"], scoped["admin"], scoped["item_a"], scoped["item_b"],
    )
    first = issue_bill(council_id=council.id, payer=payer, lines=[{"council_revenue_item": item_a, "quantity": 1}], actor=admin)

    client = authed_api_client(admin)
    r = client.post(
        "/api/v1/bills",
        {"payer_id": payer.id, "lines": [{"revenue_item_id": item_b.id, "quantity": 1}]},
        format="json",
    )
    assert r.status_code == 409, r.content
    assert r.json()["duplicate_of"]["id"] == first.id
    assert r.json()["duplicate_of"]["bill_ref"] == first.bill_ref

    r2 = client.post(
        "/api/v1/bills",
        {"payer_id": payer.id, "lines": [{"revenue_item_id": item_b.id, "quantity": 1}], "force": True},
        format="json",
    )
    assert r2.status_code == 201, r2.content
    assert r2.json()["id"] != first.id
