"""
Global reconciliation exceptions view added for the frontend handoff
(BACKEND_HANDOFF.md item 6) — a cross-run "browse everything unmatched" list,
distinct from the existing per-run nested exceptions.
"""
import datetime

import pytest
from django.db import transaction
from django.utils import timezone

from apps.payments.models import PaymentChannel
from apps.reconciliation.models import ReconciliationException, ReconciliationRun
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user):
    council = make_council(code="RCX")
    with transaction.atomic():
        set_council_context(council.id)
        admin = make_user(council, username="rcx-admin")
        channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.POS)
        run = ReconciliationRun.objects.create(
            council=council, channel=channel, run_date=datetime.date.today(), run_by=admin,
        )
        yield {"council": council, "admin": admin, "channel": channel, "run": run}


def _make_exception(scoped, resolved=False, note=""):
    exc = ReconciliationException.objects.create(
        council=scoped["council"], run=scoped["run"], note=note,
    )
    if resolved:
        exc.resolved_at = timezone.now()
        exc.resolved_by = scoped["admin"]
        exc.save(update_fields=["resolved_at", "resolved_by"])
    return exc


@pytest.mark.django_db(transaction=True)
def test_global_exceptions_defaults_to_unresolved(scoped, authed_api_client):
    unresolved = _make_exception(scoped, resolved=False)
    _make_exception(scoped, resolved=True)

    r = authed_api_client(scoped["admin"]).get("/api/v1/reconciliation/exceptions")
    assert r.status_code == 200, r.content
    ids = {row["id"] for row in r.json()}
    assert ids == {unresolved.id}


@pytest.mark.django_db(transaction=True)
def test_global_exceptions_resolved_filter(scoped, authed_api_client):
    unresolved = _make_exception(scoped, resolved=False)
    resolved = _make_exception(scoped, resolved=True)
    client = authed_api_client(scoped["admin"])

    r_true = client.get("/api/v1/reconciliation/exceptions?resolved=true")
    assert {row["id"] for row in r_true.json()} == {resolved.id}

    r_false = client.get("/api/v1/reconciliation/exceptions?resolved=false")
    assert {row["id"] for row in r_false.json()} == {unresolved.id}


@pytest.mark.django_db(transaction=True)
def test_global_exceptions_council_scoped(scoped, authed_api_client, make_council, make_user):
    _make_exception(scoped, resolved=False)

    with transaction.atomic():
        other_council = make_council(code="RCY")
        set_council_context(other_council.id)
        other_admin = make_user(other_council, username="rcy-admin")
        other_channel, _ = PaymentChannel.objects.get_or_create(code=PaymentChannel.OTC)
        other_run = ReconciliationRun.objects.create(
            council=other_council, channel=other_channel, run_date=datetime.date.today(), run_by=other_admin,
        )
        ReconciliationException.objects.create(council=other_council, run=other_run)

    r = authed_api_client(scoped["admin"]).get("/api/v1/reconciliation/exceptions")
    assert r.status_code == 200, r.content
    for row in r.json():
        assert row["run"] == scoped["run"].id


@pytest.mark.django_db(transaction=True)
def test_global_exceptions_includes_channel_and_run_date(scoped, authed_api_client):
    exc = _make_exception(scoped, resolved=False)

    r = authed_api_client(scoped["admin"]).get("/api/v1/reconciliation/exceptions")
    assert r.status_code == 200, r.content
    row = next(row for row in r.json() if row["id"] == exc.id)
    assert row["channel_code"] == PaymentChannel.POS
    assert row["run_date"] == scoped["run"].run_date.isoformat()
