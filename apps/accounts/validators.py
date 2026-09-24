"""
Password rules for every way a login gets its password.

Until now AUTH_PASSWORD_VALIDATORS only ran on a self-service change
(ChangePasswordSerializer). Every onboarding path — stakeholder, revenue
officer, field agent, consultant manager, ratepayer invite — handed whatever
the admin typed straight to create_user(), so "1", "password" and the demo
password printed in this public repository were all accepted for real accounts.
"""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

#: Substrings that make a password guessable by anyone who has read this
#: repository or the product's own name. Case-insensitive.
_PUBLISHED_FRAGMENTS = ("acrev360",)


class NotPublishedPasswordValidator:
    """Rejects passwords built on the product name — which includes the shared
    demo password (``acrev360-2026``) committed to this public repository. That
    one is 13 characters, not in Django's common-password list and not numeric,
    so none of the stock validators catch it."""

    def validate(self, password, user=None):
        lowered = password.lower()
        if any(fragment in lowered for fragment in _PUBLISHED_FRAGMENTS):
            raise DjangoValidationError(
                "This password is built on the product name and appears in public documentation — choose another.",
                code="password_published",
            )

    def get_help_text(self):
        return "Your password can't be based on the product name or any published demo password."


def validate_account_password(value):
    """DRF field validator for a password an admin sets while onboarding someone
    — same rules as a self-service change, reported as a normal 400 on the
    field."""
    try:
        validate_password(value)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages)) from None
    return value
