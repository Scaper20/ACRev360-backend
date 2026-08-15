from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class AppTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Carries `council_id` on the access token itself so apps.tenancy.middleware can
    set the RLS context by decoding the token alone — no DB query needed before the
    tenant context is known. See apps/tenancy/middleware.py.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["council_id"] = user.council_id
        token["access_level"] = user.access_level
        token["consultant_id"] = user.consultant_id
        return token
