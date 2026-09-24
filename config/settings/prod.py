from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")  # required, no default — fail loud if unset

# Fail loud on the dev-only fallback secrets rather than silently shipping
# them (base.py defaults were tuned for local dev — see the inline comments
# there). An ops deploy that forgot to set these should crash at boot, not
# serve traffic with secrets everyone in the repo can read.
if SECRET_KEY == "django-insecure-dev-only-change-me":  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set explicitly in production — refusing to run with the dev fallback.")
if WEBHOOK_ENCRYPTION_KEY == "s2r1ljZp8AW0DE6VWxIBAETFnDKbiMG16KNhI5sCLYE=":  # noqa: F405
    raise ImproperlyConfigured("WEBHOOK_ENCRYPTION_KEY must be set explicitly in production — refusing to run with the dev fallback key.")

# Enforcement-gated by env so a rollout can toggle it via DJANGO_ENV without a
# code deploy — but it must be DECIDED explicitly in production; base.py's
# default keeps it off for dev/test, and silently shipping default-off in prod
# would defeat the whole point.
ENFORCE_ACCOUNT_PASSWORD_POLICY = env.bool("ENFORCE_ACCOUNT_PASSWORD_POLICY", default=True)

# Login brute-force budget — env-overridable, strict default. Falls back to
# base's generous 30/min only if a deploy ops team explicitly relaxes it.
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "login": env("LOGIN_THROTTLE_RATE", default="10/min"),
    # See apps/accounts/throttles.py — the IP-keyed "login" scope above is
    # bypassable (X-Forwarded-For), these per-email ones are what hold.
    "login_email_burst": env("LOGIN_EMAIL_BURST_RATE", default="10/min"),
    "login_email_sustained": env("LOGIN_EMAIL_SUSTAINED_RATE", default="60/hour"),
}

# Default True for a real deploy behind a TLS-terminating proxy (nginx/ALB —
# V2_ARCHITECTURE.md §2). Set DJANGO_SECURE_SSL_REDIRECT=False for this repo's
# docker-compose.yml, which has no such proxy in front of gunicorn — with this
# left True there, every request 301s to an https:// port nothing is listening
# on.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_SSL_REDIRECT
SECURE_HSTS_PRELOAD = SECURE_SSL_REDIRECT
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

MIDDLEWARE = ["whitenoise.middleware.WhiteNoiseMiddleware", *MIDDLEWARE]  # noqa: F405
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")  # required, no default
