from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.tenancy.api.views import OnboardCouncilView, WardZoneViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"wards", WardZoneViewSet, basename="ward")

urlpatterns = router.urls + [
    path("councils/onboard", OnboardCouncilView.as_view(), name="council-onboard"),
]
