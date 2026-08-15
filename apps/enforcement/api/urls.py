from rest_framework.routers import DefaultRouter

from apps.enforcement.api.views import DebtCaseViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"debt", DebtCaseViewSet, basename="debt")

urlpatterns = router.urls
