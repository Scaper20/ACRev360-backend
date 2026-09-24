from django.core.cache import cache
from django.db.models.signals import post_delete, post_save

from apps.tenancy.context import BILL_REF_PREFIX_CACHE_KEY
from apps.tenancy.models import Council, CouncilConfig


def _drop_prefix_map(**_kwargs):
    cache.delete(BILL_REF_PREFIX_CACHE_KEY)


for _model in (Council, CouncilConfig):
    post_save.connect(_drop_prefix_map, sender=_model, dispatch_uid=f"drop-prefix-map-save-{_model.__name__}")
    post_delete.connect(_drop_prefix_map, sender=_model, dispatch_uid=f"drop-prefix-map-delete-{_model.__name__}")
