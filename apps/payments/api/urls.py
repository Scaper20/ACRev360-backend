from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.payments.api.views import (
    APIClientViewSet,
    PaymentViewSet,
    POSTerminalViewSet,
    ReceiptViewSet,
    VerifyReceiptView,
)

router = DefaultRouter(trailing_slash=False)
router.register(r"payments", PaymentViewSet, basename="payment")
router.register(r"receipts", ReceiptViewSet, basename="receipt")
router.register(r"terminals", POSTerminalViewSet, basename="terminal")
router.register(r"api-clients", APIClientViewSet, basename="api-client")

urlpatterns = router.urls + [
    path("verify/<uuid:qr_token>", VerifyReceiptView.as_view(), name="verify-receipt"),
]
