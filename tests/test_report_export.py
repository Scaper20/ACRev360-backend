"""
PR 10: ?export=csv on the existing ad-hoc reports endpoint, reusing exactly
the same aggregated rows the on-screen (JSON) report already computes — no
second query, just a different serialization at the very end of the same
view.
"""
import csv
import io

import pytest
from django.db import transaction

from apps.billing.services import issue_bill
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_ward, make_user, make_revenue_item, make_payer):
    council = make_council(code="EXP")
    with transaction.atomic():
        set_council_context(council.id)
        ward_a = make_ward(council, code="WA", name="Ward A")
        ward_b = make_ward(council, code="WB", name="Ward B")
        admin = make_user(council, username="exp-admin")
        item = make_revenue_item(council, code="EXPITEM", rate=10000)
        yield {"council": council, "ward_a": ward_a, "ward_b": ward_b, "admin": admin, "item": item}


def _parse_csv(content: bytes):
    return list(csv.DictReader(io.StringIO(content.decode())))


@pytest.mark.django_db(transaction=True)
def test_payers_report_csv_export_matches_json_rows(scoped, authed_api_client, make_payer):
    make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Payer A1", phone="08060000001")
    make_payer(scoped["council"], scoped["ward_b"], scoped["admin"], name="Payer B1", phone="08060000002")

    client = authed_api_client(scoped["admin"])
    r_json = client.get("/api/v1/reports?entity=PAYERS&group_by=ward")
    assert r_json.status_code == 200, r_json.content

    r_csv = client.get("/api/v1/reports?entity=PAYERS&group_by=ward&export=csv")
    assert r_csv.status_code == 200, r_csv.content
    assert r_csv["Content-Type"] == "text/csv"
    assert "attachment" in r_csv["Content-Disposition"]

    csv_rows = {row["ward"]: int(row["count"]) for row in _parse_csv(r_csv.content)}
    json_rows = {row["ward"]: row["count"] for row in r_json.json()["rows"]}
    assert csv_rows == json_rows


@pytest.mark.django_db(transaction=True)
def test_bills_report_csv_export_reflects_filtered_rows(scoped, authed_api_client, make_payer):
    payer_a = make_payer(scoped["council"], scoped["ward_a"], scoped["admin"], name="Bill Payer A", phone="08060000003")
    payer_b = make_payer(scoped["council"], scoped["ward_b"], scoped["admin"], name="Bill Payer B", phone="08060000004")
    issue_bill(council_id=scoped["council"].id, payer=payer_a, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])
    issue_bill(council_id=scoped["council"].id, payer=payer_b, lines=[{"council_revenue_item": scoped["item"], "quantity": 1}], actor=scoped["admin"])

    client = authed_api_client(scoped["admin"])
    r_csv = client.get(f"/api/v1/reports?entity=BILLS&ward_id={scoped['ward_a'].id}&export=csv")
    assert r_csv.status_code == 200, r_csv.content
    rows = _parse_csv(r_csv.content)
    assert len(rows) == 1
    assert rows[0]["count"] == "1"
    assert rows[0]["billed"] == "10000.00"


@pytest.mark.django_db(transaction=True)
def test_csv_export_with_no_matching_rows_returns_placeholder(scoped, authed_api_client):
    # Grouped with zero matching payers is the case that actually produces an
    # empty rows list — ungrouped always returns one row (count=0), since
    # that's a plain aggregate, not a group-by.
    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=PAYERS&group_by=ward&export=csv")
    assert r.status_code == 200, r.content
    rows = _parse_csv(r.content)
    assert rows == [{"message": "No data for the selected filters"}]


@pytest.mark.django_db(transaction=True)
def test_invalid_format_param_is_rejected(scoped, authed_api_client):
    r = authed_api_client(scoped["admin"]).get("/api/v1/reports?entity=PAYERS&export=xlsx")
    assert r.status_code == 400, r.content
