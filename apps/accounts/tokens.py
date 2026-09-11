from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings


class AppTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Carries `council_id` on the access token itself so apps.tenancy.middleware can
    set the RLS context by decoding the token alone — no DB query needed before the
    tenant context is known. See apps/tenancy/middleware.py.

    Logs in by email, not username — but AppUser.USERNAME_FIELD stays
    "username" deliberately (lower blast radius: Django admin/permissions
    internals key off USERNAME_FIELD too, and there's no reason to touch
    those). So this can't just set `username_field = "email"` and let the
    parent's validate() call authenticate(email=...) — Django's ModelBackend
    only ever looks for the USERNAME_FIELD name ("username") in the kwargs it
    receives, and would silently fail to authenticate anyone. Instead: resolve
    the given email to its underlying username first, then authenticate and
    build tokens exactly the way the stock username/password flow always did.
    """

    username_field = "email"

    def validate(self, attrs):
        email = attrs.get("email", "")
        user = get_user_model().objects.filter(email__iexact=email).first() if email else None
        authenticate_kwargs = {"username": user.username if user else email, "password": attrs["password"]}
        try:
            authenticate_kwargs["request"] = self.context["request"]
        except KeyError:
            pass
        self.user = authenticate(**authenticate_kwargs)
        if not api_settings.USER_AUTHENTICATION_RULE(self.user):
            raise AuthenticationFailed(self.error_messages["no_active_account"], "no_active_account")

        data = {}
        refresh = self.get_token(self.user)
        data["refresh"] = str(refresh)
        data["access"] = str(refresh.access_token)
        if api_settings.UPDATE_LAST_LOGIN:
            update_last_login(None, self.user)
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["council_id"] = user.council_id
        token["access_level"] = user.access_level
        token["consultant_id"] = user.consultant_id
        payer_profile = getattr(user, "payer_profile", None)
        token["payer_id"] = payer_profile.id if payer_profile else None
        return token
