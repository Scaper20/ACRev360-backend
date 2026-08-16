"""
Synchronous entry point for the daily debt re-ageing job, for deployments that
schedule it via an external cron (e.g. Render Cron Jobs) instead of running a
standing Celery worker + beat + broker. Calls the same
apps.enforcement.tasks.refresh_all_councils_debt task function directly — a
Celery @shared_task is a plain callable when invoked without .delay()/
.apply_async(), so this runs the identical logic with no broker involved.

Once real async workloads exist (webhook post-processing, etc.), switch back to
Celery beat (see docker-compose.yml's celery-beat service) and drop this command.
"""
from django.core.management.base import BaseCommand

from apps.enforcement.tasks import refresh_all_councils_debt


class Command(BaseCommand):
    help = "Refresh debt ageing/buckets for every active council (run daily)."

    def handle(self, *args, **options):
        results = refresh_all_councils_debt()
        for council_code, result in results.items():
            self.stdout.write(f"{council_code}: {result}")
