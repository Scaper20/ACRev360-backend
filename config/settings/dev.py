from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True

# Never a real cap in dev/test — the 300+-test suite shares one testserver IP,
# and a rolling 1-minute budget would trip on consecutive login tests. The
# throttle mechanism itself still runs here; only the budget is effectively
# unlimited. prod.py sets the real (env-required, strict-default) numbers.
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # noqa: F405
    "login": "100000/hour",
}

# Convenient for local `manage.py runserver` against the locally-installed Postgres 18
# instance rather than requiring Docker just to run the dev server.
