FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

RUN adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
# Shell form (not exec-array) so ${PORT:-8000}/${WEB_CONCURRENCY:-1} expand —
# Render assigns PORT at runtime and expects the container to bind to it;
# docker-compose.yml overrides this CMD with a fixed port/worker count for
# local dev, so that path is unaffected. WEB_CONCURRENCY must be respected,
# not hardcoded: Render's free-tier instance logs "Setting WEB_CONCURRENCY=1
# by default, based on available CPUs in the instance" and a hardcoded
# --workers 3 ignored that entirely — 3 full Django worker processes appear
# to have exceeded the free instance's memory, silently OOM-killing the
# container moments after gunicorn logged all 3 as booted (no Python
# traceback, since the kernel kills it — the deploy just hung forever after
# with the port never becoming reachable). Confirmed live: three consecutive
# deploys reproduced the identical hang at the identical point.
#
# The fallback default itself was still 3 until this line — self-inconsistent
# with the very log line this comment quotes (Render recommends 1 on free
# tier). Harmless whenever Render actually injects WEB_CONCURRENCY (the
# fallback never triggers), but silently reproduces the exact OOM-kill above
# on any environment where it isn't set. Confirmed live on a second, separate
# Render account/service: identical symptom — one "Booting worker" line
# logged, then silence forever, health check never passing.
CMD gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1}
