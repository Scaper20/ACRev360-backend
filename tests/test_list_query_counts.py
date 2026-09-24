"""
List endpoints must cost a constant number of queries however many rows they
return. A live audit against production data found several that scaled per
row (receipts 210 queries for 50 rows, payers 78, payments 61, revenue-item
templates 39, consultants 17, debt cases 15) — tests use the payer list here
because PayerSerializer.consultant_name added two hops per row when it shipped
(enumerated_by -> consultant) and nobody noticed until the audit.

The check is relative — query count with 1 row vs with several — so it holds
regardless of how many fixed queries auth/RLS setup adds.
"""
import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.tenancy.context import set_council_context


def _count(client, url):
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, response.content
    return len(ctx)


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_consultant):
    council = make_council(code="QCT")
    with transaction.atomic():
        set_council_context(council.id)
        ward = make_ward(council)
        admin = make_user(council, username="qct-admin")
        consultant = make_consultant(council, name="QCT Co", contract_ref="CR-QCT")
        yield {"council": council, "ward": ward, "admin": admin, "consultant": consultant}


@pytest.mark.django_db(transaction=True)
def test_payer_list_query_count_does_not_grow_with_rows(scoped, authed_api_client, make_user, make_payer):
    from apps.accounts.models import AppRole

    council, ward = scoped["council"], scoped["ward"]
    client = authed_api_client(scoped["admin"])

    with transaction.atomic():
        set_council_context(council.id)
        make_payer(council, ward, scoped["admin"], name="Solo Payer", phone="08010000001")
    one_row = _count(client, "/api/v1/payers")

    # Each new payer is enumerated by a *different* consultant-linked user, so
    # an unjoined enumerated_by -> consultant walk costs queries per row.
    with transaction.atomic():
        set_council_context(council.id)
        for i in range(2, 8):
            agent = make_user(
                council, username=f"qct-agent-{i}", access_level=AppRole.AGENT, consultant=scoped["consultant"],
            )
            make_payer(council, ward, agent, name=f"Payer {i}", phone=f"0801000{i:04d}")
    many_rows = _count(client, "/api/v1/payers")

    assert many_rows == one_row, f"payer list grew from {one_row} to {many_rows} queries with more rows — N+1"
