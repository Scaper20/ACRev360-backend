from rest_framework.routers import DefaultRouter

from apps.reconciliation.api.views import ReconciliationRunViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"reconciliation", ReconciliationRunViewSet, basename="reconciliation")

urlpatterns = router.urls
