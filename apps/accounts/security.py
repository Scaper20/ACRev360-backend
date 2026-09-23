"""
Password provisioning — the ENFORCE_ACCOUNT_PASSWORD_POLICY behaviour. When the
policy is on, any account created without an explicit password gets a strong
system-generated one (returned to the creator in the create response, exactly
once) and must_change_password=True, so the person logging in is forced to pick
their own before anything else happens. With the policy off (dev/test, which
share the "acrev360-2026" fallback deliberately) behaviour is unchanged.
"""
import secrets

from django.conf import settings

#: Ambiguity-safe alphabet: no 0/O or 1/l/I, so a password read off a screen or
#: relayed over the phone can't be mistyped.
_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def enforce_password_policy() -> bool:
    return settings.ENFORCE_ACCOUNT_PASSWORD_POLICY


def provision_password(explicit_password):
    """Resolve an onboarding password: honour an explicitly supplied one; when
    the account policy is on and none was supplied, generate a strong one that
    the account must change on first login. Returns (password, must_change)."""
    if enforce_password_policy() and not explicit_password:
        return generate_password(), True
    return explicit_password or "acrev360-2026", False