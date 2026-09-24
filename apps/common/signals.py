"""
Cache invalidation: which writes make which cached read model stale.
Connected in CommonConfig.ready() by app label so this module never imports
another app's models at import time.

Only saves/deletes that go through the ORM's model API fire these; a bulk
``queryset.update()`` or another process (the nightly debt-ageing job) does not,
which is what apps.common.cachekeys.TOKEN_TTL bounds.
"""
from django.apps import apps
from django.db.models.signals import post_delete, post_save

from apps.common import cachekeys

#: Models whose rows feed the dashboard totals, keyed per council.
_DASHBOARD_MODELS = (
    "billing.Bill", "billing.Assessment", "payments.Payment", "registry.Payer",
    "accounts.FieldAgent", "settlements.CommissionSettlement",
)

#: Models that make up the revenue-item catalogue a client downloads. One token
#: for every council — catalogue edits are rare, and resolving the owning
#: council of a rate tier costs two extra queries for nothing.
_CATALOGUE_MODELS = (
    "revenue.CouncilRevenueItem", "revenue.RateSchedule", "revenue.RateBand", "revenue.RateTier",
    "revenue.ConsultantPortfolio", "revenue.AgentPortfolio", "tenancy.Department",
)


def _bump_dashboard(sender, instance, **_kwargs):
    council_id = getattr(instance, "council_id", None)
    if council_id is not None:
        cachekeys.bump("dashboard", council_id)


def _bump_catalogue(sender, instance, **_kwargs):
    cachekeys.bump("catalogue")


def connect():
    for label in _DASHBOARD_MODELS:
        model = apps.get_model(label)
        for signal in (post_save, post_delete):
            signal.connect(_bump_dashboard, sender=model, dispatch_uid=f"dash-{label}-{signal.__class__.__name__}-{id(signal)}")
    for label in _CATALOGUE_MODELS:
        model = apps.get_model(label)
        for signal in (post_save, post_delete):
            signal.connect(_bump_catalogue, sender=model, dispatch_uid=f"cat-{label}-{signal.__class__.__name__}-{id(signal)}")
