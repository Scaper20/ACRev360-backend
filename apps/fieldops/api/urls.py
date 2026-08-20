from django.urls import path

from apps.fieldops.api.views import SyncView, WorklistView

urlpatterns = [
    path("mobile/worklist", WorklistView.as_view(), name="mobile-worklist"),
    path("mobile/sync", SyncView.as_view(), name="mobile-sync"),
]
