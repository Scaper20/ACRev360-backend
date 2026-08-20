from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.api.views import (
    FieldAgentViewSet,
    LoginView,
    LogoutView,
    MeView,
    StakeholderViewSet,
    SubConsultantViewSet,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"consultants", SubConsultantViewSet, basename="consultant")
router.register(r"agents", FieldAgentViewSet, basename="agent")
router.register(r"stakeholders", StakeholderViewSet, basename="stakeholder")

auth_urlpatterns = [
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", LogoutView.as_view(), name="auth-logout"),
    path("auth/me", MeView.as_view(), name="auth-me"),
]

urlpatterns = auth_urlpatterns + router.urls
