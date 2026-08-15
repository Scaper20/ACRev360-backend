from django.urls import path

from apps.channels.api.views import (
    ChannelCatalogueView,
    OTCSettlementView,
    USSDSessionView,
    WebhookView,
)

urlpatterns = [
    path("channels", ChannelCatalogueView.as_view(), name="channel-catalogue"),
    path("channels/<str:code>/webhook", WebhookView.as_view(), name="channel-webhook"),
    path("channels/OTC/settlement", OTCSettlementView.as_view(), name="otc-settlement"),
    path("channels/USSD/session", USSDSessionView.as_view(), name="ussd-session"),
]
