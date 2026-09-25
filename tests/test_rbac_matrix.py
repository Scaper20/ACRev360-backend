"""
Executable source of truth for "who can hit what" — docs/RBAC_EXPANSION_DESIGN.md
cites this file by name as the exhaustive per-endpoint table; this is that file.

Built after a real gap it would have caught shipped anyway: the frontend team
tested the live `qa_council_*` production accounts against the actual API and
found PayerViewSet 403ing for COUNCIL_IGR_HEAD/COUNCIL_TREASURY/COUNCIL_AUDITOR
(missed entirely in the original pass) and COUNCIL_IT unable to list agents/
consultants despite being able to create both (get_permissions()'s create
branch had it, the list/retrieve branch didn't) — see the 2026-09-11 CHANGELOG
entries. Both are fixed; this file exists so the next one doesn't need a
frontend engineer logging into production to find it.

Two ways a permission set is asserted here, matching how DRF actually resolves
it at request time:
  - A get_permissions()-branch (list/retrieve/create/delete, keyed off
    self.action or self.request.method): construct the view directly, set
    .action/.request.method, call get_permissions().
  - An @action(..., permission_classes=[...])-decorated method: DRF reassigns
    self.permission_classes from the bound method's own .kwargs before
    get_permissions() runs, which a bare `ViewClass()` instantiation does NOT
    replicate — so those are read directly off the method's .kwargs instead.
"""
from types import SimpleNamespace

import pytest

from apps.accounts.api.views import FieldAgentViewSet, StakeholderViewSet, SubConsultantViewSet
from apps.accounts.models import AppRole as R
from apps.audit.api.views import AuditLogViewSet
from apps.billing.api.views import BillViewSet
from apps.common.api.dashboard import DashboardGlobalView
from apps.common.api.reports import _ENTITY_LEVELS, BILLS, PAYERS, PAYMENTS, SETTLEMENTS
from apps.enforcement.api.views import DebtCaseViewSet
from apps.payments.api.views import APIClientViewSet, PaymentViewSet, ReceiptViewSet
from apps.reconciliation.api.views import ReconciliationRunViewSet
from apps.registry.api.views import PayerViewSet
from apps.revenue.api.views import CouncilRevenueItemViewSet, READ_ONLY_LEVELS
from apps.settlements.api.views import CommissionSettlementViewSet
from apps.tenancy.api.views import DepartmentViewSet, WardZoneViewSet


def _extract_levels(permission_classes):
    levels = set()
    for entry in permission_classes:
        instance = entry() if isinstance(entry, type) else entry
        if hasattr(instance, "allowed_levels"):
            levels.update(instance.allowed_levels)
    return levels


def _via_get_permissions(view_cls, *, action=None, method="GET"):
    """For a view whose get_permissions() branches on self.action/self.request.method."""
    view = view_cls()
    view.action = action
    view.request = SimpleNamespace(method=method)
    return _extract_levels(view.get_permissions())


def _via_action_decorator(view_cls, method_name):
    """For an @action(..., permission_classes=[...])-decorated method — read
    straight off the bound method, since only DRF's real dispatch (not a bare
    instantiation) reassigns self.permission_classes from it."""
    bound = getattr(view_cls, method_name)
    permission_classes = bound.kwargs.get("permission_classes")
    assert permission_classes is not None, f"{view_cls.__name__}.{method_name} has no permission_classes override"
    return _extract_levels(permission_classes)


def _via_class_attribute(view_cls):
    """For a view with a plain, unbranched class-level permission_classes."""
    return _extract_levels(view_cls.permission_classes)


# (description, actual-levels-callable, expected frozenset)
CASES = [
    # --- Payers ---
    (
        "PayerViewSet list/retrieve",
        lambda: _via_get_permissions(PayerViewSet, action="list", method="GET"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
         R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR},
    ),
    (
        "PayerViewSet create",
        lambda: _via_get_permissions(PayerViewSet, action="create", method="POST"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT},
    ),
    (
        "PayerViewSet DELETE",
        lambda: _via_get_permissions(PayerViewSet, action="destroy", method="DELETE"),
        {R.COUNCIL_ADMIN},
    ),
    ("PayerViewSet.kyc_status", lambda: _via_action_decorator(PayerViewSet, "kyc_status"), {R.COUNCIL_ADMIN}),
    (
        "PayerViewSet.invite_ratepayer",
        lambda: _via_action_decorator(PayerViewSet, "invite_ratepayer"),
        {R.COUNCIL_ADMIN, R.COUNCIL_IT, R.AGENT},
    ),

    # --- Bills ---
    (
        "BillViewSet list/retrieve",
        lambda: _via_get_permissions(BillViewSet, action="list", method="GET"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
         R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR},
    ),
    (
        "BillViewSet create",
        lambda: _via_get_permissions(BillViewSet, action="create", method="POST"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT},
    ),
    ("BillViewSet DELETE", lambda: _via_get_permissions(BillViewSet, action="destroy", method="DELETE"), {R.COUNCIL_ADMIN}),
    ("BillViewSet.add_line", lambda: _via_action_decorator(BillViewSet, "add_line"), {R.COUNCIL_ADMIN}),
    ("BillViewSet.line_detail", lambda: _via_action_decorator(BillViewSet, "line_detail"), {R.COUNCIL_ADMIN}),

    # --- Payments / Receipts / APIClient ---
    (
        "PaymentViewSet list/retrieve",
        lambda: _via_get_permissions(PaymentViewSet, action="list", method="GET"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
         R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR},
    ),
    (
        "PaymentViewSet create",
        lambda: _via_get_permissions(PaymentViewSet, action="create", method="POST"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT},
    ),
    ("PaymentViewSet.reverse", lambda: _via_action_decorator(PaymentViewSet, "reverse"), {R.COUNCIL_ADMIN}),
    (
        "ReceiptViewSet list",
        lambda: _via_get_permissions(ReceiptViewSet, action="list", method="GET"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
         R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR},
    ),
    (
        "ReceiptViewSet.send",
        lambda: _via_get_permissions(ReceiptViewSet, action="send", method="POST"),
        {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT},
    ),
    (
        "APIClientViewSet create",
        lambda: _via_get_permissions(APIClientViewSet, action="create", method="POST"),
        {R.COUNCIL_ADMIN},
    ),
    (
        "APIClientViewSet list/retrieve/revoke",
        lambda: _via_get_permissions(APIClientViewSet, action="list", method="GET"),
        {R.COUNCIL_ADMIN, R.DEVOPS_ADMIN},
    ),

    # --- Debt / Reconciliation / Settlements / Audit ---
    ("DebtCaseViewSet list", lambda: _via_class_attribute(DebtCaseViewSet),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY, R.COUNCIL_AUDITOR,
      R.CONSULTANT_STAFF}),
    ("DebtCaseViewSet.refresh", lambda: _via_action_decorator(DebtCaseViewSet, "refresh"), {R.COUNCIL_ADMIN}),
    ("DebtCaseViewSet.escalate", lambda: _via_action_decorator(DebtCaseViewSet, "escalate"), {R.COUNCIL_ADMIN}),
    # CONSULTANT deliberately excluded — see ReconciliationRunViewSet's own
    # docstring: every figure here (total_platform, total_bank, unmatched
    # credits, live-summary, exceptions) is a whole-council bank-vs-platform
    # match with no per-consultant reading to scope down to, not portfolio
    # data. Confirmed with the client, 2026-09.
    ("ReconciliationRunViewSet list", lambda: _via_class_attribute(ReconciliationRunViewSet),
     {R.COUNCIL_ADMIN, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY, R.COUNCIL_AUDITOR}),
    ("ReconciliationRunViewSet.run", lambda: _via_action_decorator(ReconciliationRunViewSet, "run"),
     {R.COUNCIL_ADMIN, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY}),
    ("ReconciliationRunViewSet.resolve_exception", lambda: _via_action_decorator(ReconciliationRunViewSet, "resolve_exception"),
     {R.COUNCIL_ADMIN, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY}),
    ("CommissionSettlementViewSet list", lambda: _via_class_attribute(CommissionSettlementViewSet),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.REVENUE_OFFICER, R.COUNCIL_TREASURY, R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF,
      R.FINANCE_ADMIN}),
    ("CommissionSettlementViewSet.compute", lambda: _via_action_decorator(CommissionSettlementViewSet, "compute"), {R.COUNCIL_ADMIN}),
    ("CommissionSettlementViewSet.status_change", lambda: _via_action_decorator(CommissionSettlementViewSet, "status_change"), {R.COUNCIL_ADMIN}),
    ("AuditLogViewSet list", lambda: _via_class_attribute(AuditLogViewSet),
     {R.COUNCIL_ADMIN, R.COUNCIL_IGR_HEAD, R.COUNCIL_AUDITOR, R.COMPLIANCE_VIEW, R.EXTERNAL_AUDITOR, R.SUPER_ADMIN,
      R.PLATFORM_ADMIN}),

    # --- Field agents / Sub-consultants / Stakeholders ---
    ("FieldAgentViewSet create", lambda: _via_get_permissions(FieldAgentViewSet, action="create", method="POST"),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.COUNCIL_IT}),
    ("FieldAgentViewSet list/retrieve", lambda: _via_get_permissions(FieldAgentViewSet, action="list", method="GET"),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.COUNCIL_IT, R.CONSULTANT_STAFF, R.COUNCIL_AUDITOR, R.COUNCIL_IGR_HEAD,
      R.AGENT_SUPERVISOR}),
    ("FieldAgentViewSet.assign_payer", lambda: _via_action_decorator(FieldAgentViewSet, "assign_payer"),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT_SUPERVISOR}),
    ("FieldAgentViewSet.activity", lambda: _via_action_decorator(FieldAgentViewSet, "activity"),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.AGENT_SUPERVISOR}),
    ("FieldAgentViewSet.portfolio (no override -> class default)",
     lambda: _via_get_permissions(FieldAgentViewSet, action="portfolio", method="GET"),
     {R.COUNCIL_ADMIN, R.CONSULTANT}),
    ("SubConsultantViewSet create (no override -> class default)",
     lambda: _via_get_permissions(SubConsultantViewSet, action="create", method="POST"), {R.COUNCIL_ADMIN}),
    ("SubConsultantViewSet list/retrieve", lambda: _via_get_permissions(SubConsultantViewSet, action="list", method="GET"),
     {R.COUNCIL_ADMIN, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY, R.COUNCIL_AUDITOR, R.COUNCIL_IT, R.COMPLIANCE_VIEW,
      R.EXTERNAL_AUDITOR, R.SUPER_ADMIN, R.PLATFORM_ADMIN}),
    ("SubConsultantViewSet.status_change", lambda: _via_action_decorator(SubConsultantViewSet, "status_change"), {R.COUNCIL_ADMIN}),
    ("SubConsultantViewSet.contract_dates", lambda: _via_action_decorator(SubConsultantViewSet, "contract_dates"), {R.COUNCIL_ADMIN}),
    ("SubConsultantViewSet.revenue_officers", lambda: _via_action_decorator(SubConsultantViewSet, "revenue_officers"),
     {R.COUNCIL_ADMIN, R.COUNCIL_IT}),
    ("SubConsultantViewSet.portfolio", lambda: _via_action_decorator(SubConsultantViewSet, "portfolio"),
     {R.COUNCIL_ADMIN, R.CONSULTANT}),
    ("StakeholderViewSet", lambda: _via_class_attribute(StakeholderViewSet), {R.COUNCIL_ADMIN, R.COUNCIL_IT}),

    # --- Tenancy reference data ---
    ("WardZoneViewSet GET", lambda: _via_get_permissions(WardZoneViewSet, method="GET"),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.GLOBAL_VIEW, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
      R.COUNCIL_AUDITOR, R.COUNCIL_IT, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR}),
    ("WardZoneViewSet POST", lambda: _via_get_permissions(WardZoneViewSet, method="POST"), {R.COUNCIL_ADMIN}),
    ("DepartmentViewSet GET", lambda: _via_get_permissions(DepartmentViewSet, method="GET"),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.GLOBAL_VIEW, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
      R.COUNCIL_AUDITOR, R.COUNCIL_IT, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR}),
    ("DepartmentViewSet POST", lambda: _via_get_permissions(DepartmentViewSet, method="POST"), {R.COUNCIL_ADMIN}),

    # --- Dashboard / Revenue read-only ---
    ("DashboardGlobalView", lambda: _via_class_attribute(DashboardGlobalView),
     {R.COUNCIL_ADMIN, R.GLOBAL_VIEW, R.SUPER_ADMIN, R.PLATFORM_ADMIN, R.BD_VIEW, R.COMPLIANCE_VIEW, R.ANALYTICS_VIEW,
      R.FINANCE_ADMIN, R.EXTERNAL_AUDITOR}),
    ("Revenue READ_ONLY_LEVELS", lambda: set(READ_ONLY_LEVELS),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.GLOBAL_VIEW, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
      R.COUNCIL_AUDITOR, R.COUNCIL_IT, R.CONSULTANT_STAFF, R.AGENT_SUPERVISOR}),
    ("CouncilRevenueItemViewSet.rate", lambda: _via_action_decorator(CouncilRevenueItemViewSet, "rate"), {R.COUNCIL_ADMIN}),
    ("CouncilRevenueItemViewSet.rate_bands", lambda: _via_action_decorator(CouncilRevenueItemViewSet, "rate_bands"), {R.COUNCIL_ADMIN}),
    ("CouncilRevenueItemViewSet.department", lambda: _via_action_decorator(CouncilRevenueItemViewSet, "department"), {R.COUNCIL_ADMIN}),

    # --- Reports (per-entity, apps/common/api/reports.py) ---
    ("ReportsView PAYERS", lambda: set(_ENTITY_LEVELS[PAYERS]),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
      R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.SUPER_ADMIN, R.PLATFORM_ADMIN, R.FINANCE_ADMIN, R.ANALYTICS_VIEW,
      R.COMPLIANCE_VIEW}),
    ("ReportsView BILLS", lambda: set(_ENTITY_LEVELS[BILLS]),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
      R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.SUPER_ADMIN, R.PLATFORM_ADMIN, R.FINANCE_ADMIN, R.ANALYTICS_VIEW,
      R.COMPLIANCE_VIEW}),
    ("ReportsView PAYMENTS", lambda: set(_ENTITY_LEVELS[PAYMENTS]),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.AGENT, R.REVENUE_OFFICER, R.COUNCIL_IGR_HEAD, R.COUNCIL_TREASURY,
      R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF, R.SUPER_ADMIN, R.PLATFORM_ADMIN, R.FINANCE_ADMIN, R.ANALYTICS_VIEW,
      R.COMPLIANCE_VIEW}),
    ("ReportsView SETTLEMENTS", lambda: set(_ENTITY_LEVELS[SETTLEMENTS]),
     {R.COUNCIL_ADMIN, R.CONSULTANT, R.REVENUE_OFFICER, R.COUNCIL_TREASURY, R.COUNCIL_AUDITOR, R.CONSULTANT_STAFF,
      R.SUPER_ADMIN, R.PLATFORM_ADMIN, R.FINANCE_ADMIN, R.ANALYTICS_VIEW, R.COMPLIANCE_VIEW, R.BD_VIEW,
      R.EXTERNAL_AUDITOR}),
]


@pytest.mark.parametrize("description,get_actual,expected", CASES, ids=[c[0] for c in CASES])
def test_permission_matrix(description, get_actual, expected):
    assert get_actual() == expected, description


def test_no_case_grants_ratepayer_levels():
    """Belt-and-suspenders alongside tests/test_rbac_closed_world.py's
    source-scan — RATEPAYER/RATEPAYER_PROXY must never show up as an
    *actual* allowed level on any staff-facing endpoint covered above."""
    for description, get_actual, _expected in CASES:
        actual = get_actual()
        assert R.RATEPAYER not in actual, description
        assert R.RATEPAYER_PROXY not in actual, description
