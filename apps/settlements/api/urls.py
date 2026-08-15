from rest_framework.routers import DefaultRouter

from apps.settlements.api.views import CommissionSettlementViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"settlements", CommissionSettlementViewSet, basename="settlement")

urlpatterns = router.urls
