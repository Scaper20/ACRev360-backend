from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.billing.api.views import BillViewSet, PublicBillLookupView

router = DefaultRouter(trailing_slash=False)
router.register(r"bills", BillViewSet, basename="bill")

# Router patterns (numeric pk) are tried first; the public, ref-based lookup
# (which contains slashes, e.g. KAC/2026/000123) is a catch-all and must come last.
urlpatterns = router.urls + [
    path("bills/<path:bill_ref>", PublicBillLookupView.as_view(), name="public-bill-lookup"),
]
