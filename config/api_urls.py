from django.urls import include, path

from apps.common.api.dashboard import DashboardGlobalView, DashboardSummaryView
from apps.common.api.health import HealthView

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path("", include("apps.accounts.api.urls")),
    path("", include("apps.tenancy.api.urls")),
    path("", include("apps.registry.api.urls")),
    path("", include("apps.revenue.api.urls")),
    path("", include("apps.billing.api.urls")),
    path("", include("apps.payments.api.urls")),
    path("", include("apps.channels.api.urls")),
    path("", include("apps.reconciliation.api.urls")),
    path("", include("apps.settlements.api.urls")),
    path("", include("apps.enforcement.api.urls")),
    path("", include("apps.audit.api.urls")),
    path("", include("apps.fieldops.api.urls")),
    path("dashboard/summary", DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("dashboard/global", DashboardGlobalView.as_view(), name="dashboard-global"),
]
