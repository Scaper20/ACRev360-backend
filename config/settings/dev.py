from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True

# Convenient for local `manage.py runserver` against the locally-installed Postgres 18
# instance rather than requiring Docker just to run the dev server.
