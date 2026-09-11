from rest_framework.routers import DefaultRouter

from apps.registry.api.views import EnumeratedAssetViewSet, PayerViewSet, RatepayerPortalViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"payers", PayerViewSet, basename="payer")
router.register(r"assets", EnumeratedAssetViewSet, basename="asset")
router.register(r"my", RatepayerPortalViewSet, basename="ratepayer-portal")

urlpatterns = router.urls
