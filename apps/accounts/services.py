import re

from apps.accounts.models import AppUser

#: Everything an email's local-part may contain that AppUser.username also
#: tolerates unchanged; anything else (a "+" tag, unicode, etc.) is dropped
#: rather than rejected — the email itself is what's actually validated as
#: input, the derived username just needs to be a legal, unique value.
_USERNAME_UNSAFE = re.compile(r"[^a-z0-9._-]")


def derive_username_from_email(email: str) -> str:
    """Turns an onboarding email's local-part into a value for AppUser.username
    ("jane.doe@x.com" -> "jane.doe") — the login stays email-based (see
    AppTokenObtainPairSerializer), but USERNAME_FIELD deliberately stays
    "username" per AppUser's own docstring, and it's DB-unique, so every
    create_user() call still needs one. A numeric suffix is appended on
    collision so this is never shown on an onboarding form again."""
    local_part = email.split("@", 1)[0].strip().lower()
    base = _USERNAME_UNSAFE.sub("", local_part).strip("._-")[:55] or "user"
    username = base
    suffix = 1
    while AppUser.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base}{suffix}"
    return username
