"""
Settings shared by every environment. Environment-specific overrides live in
dev.py / prod.py — never branch on "which council" or "which environment" inside
application code (see V2_ARCHITECTURE.md §4.2); settings files are the one place
environment differences are allowed to live.
"""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Application definition
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
]

LOCAL_APPS = [
    "apps.tenancy",
    "apps.accounts",
    "apps.registry",
    "apps.revenue",
    "apps.billing",
    "apps.payments",
    "apps.channels",
    "apps.reconciliation",
    "apps.settlements",
    "apps.enforcement",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Sets the Postgres session-local council_id (SET LOCAL app.council_id = ...)
    # for row-level security, from the authenticated request's tenant context.
    # See V2_ARCHITECTURE.md §3 and apps/tenancy/middleware.py.
    "apps.tenancy.middleware.CouncilContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database — PostgreSQL only. Row-level security policies (apps/tenancy) require
# it; see V2_ARCHITECTURE.md §2/§3 for why SQLite was ruled out for this rewrite.
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://acrev360:acrev360@localhost:5432/acrev360"),
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False  # tenancy middleware manages its own atomic block

AUTH_USER_MODEL = "accounts.AppUser"

# argon2 first — v1's blocking gap (unsalted SHA-256) does not survive the rewrite.
# See V2_ARCHITECTURE.md §8.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST framework -----------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "EXCEPTION_HANDLER": "apps.common.exceptions.acrev360_exception_handler",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# Auth mechanism: short-lived JWT (access + refresh), decided in V2_ARCHITECTURE.md
# §12 to be resolved "when fieldops is built" — since fieldops (mobile offline sync)
# is deferred out of this build pass, JWT is chosen now because it works unchanged
# for both the web SPA today and the mobile PWA's offline window later.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ACRev360 API",
    "DESCRIPTION": "Revenue administration & collection platform for FCT Area Councils.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# --- CORS ----------------------------------------------------------------
# Required now (unlike the v1 single-Flask-process build): frontend and backend
# are separate origins/repos. See V2_ARCHITECTURE.md.
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:5173"])
CORS_ALLOW_CREDENTIALS = True

# --- Celery ----------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "refresh-debt-ageing-daily": {
        "task": "apps.enforcement.tasks.refresh_all_councils_debt",
        "schedule": 60 * 60 * 24,
    },
}

# --- Webhook signature verification --------------------------------------
# On by default in v2 — v1 shipped this off by default behind an env flag
# (see TDD.md §8, "Blocking" gaps). Only relax for local dev if explicitly needed.
WEBHOOK_STRICT_SIGNATURES = env.bool("WEBHOOK_STRICT_SIGNATURES", default=True)

# Dev-only fallback key — every real environment must set this explicitly
# (see .env.example). Used to reversibly encrypt APIClient webhook secrets.
WEBHOOK_ENCRYPTION_KEY = env(
    "WEBHOOK_ENCRYPTION_KEY", default="s2r1ljZp8AW0DE6VWxIBAETFnDKbiMG16KNhI5sCLYE="
)
