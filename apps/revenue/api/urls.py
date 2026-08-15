from rest_framework.routers import DefaultRouter

from apps.revenue.api.views import (
    CouncilRevenueItemViewSet,
    RevenueCategoryViewSet,
    RevenueItemTemplateViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"revenue-categories", RevenueCategoryViewSet, basename="revenue-category")
router.register(r"revenue-item-templates", RevenueItemTemplateViewSet, basename="revenue-item-template")
router.register(r"revenue-items", CouncilRevenueItemViewSet, basename="revenue-item")

urlpatterns = router.urls
