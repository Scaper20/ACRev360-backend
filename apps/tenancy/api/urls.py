from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.tenancy.api.views import DepartmentViewSet, OnboardCouncilView, WardZoneViewSet

router = DefaultRouter(trailing_slash=False)
router.register(r"wards", WardZoneViewSet, basename="ward")
router.register(r"departments", DepartmentViewSet, basename="department")

urlpatterns = router.urls + [
    path("councils/onboard", OnboardCouncilView.as_view(), name="council-onboard"),
]
