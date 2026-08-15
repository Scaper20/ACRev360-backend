"""
Scheduled debt re-ageing — replaces the prototype's manual "Refresh Ageing"
button (V2_ARCHITECTURE.md §9). Runs off the request path via Celery beat.
"""
from celery import shared_task

from apps.enforcement.services import refresh_debt
from apps.tenancy.context import council_context
from apps.tenancy.models import Council


@shared_task
def refresh_all_councils_debt():
    results = {}
    for council in Council.objects.filter(is_active=True):
        with council_context(council.id):
            results[council.council_code] = refresh_debt(council_id=council.id, actor=None)
    return results
