"""
Changing a password must end other sessions. Refresh tokens live 7 days and
rotate, so without revocation an attacker who already holds one keeps access
straight through the victim rotating a compromised password.
"""
import pytest
from django.db import transaction

from apps.tenancy.context import set_council_context

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
CHANGE = "/api/v1/auth/change-password"


@pytest.fixture
def account(make_council, make_user):
    council = make_council(code="SRV")
    with transaction.atomic():
        set_council_context(council.id)
        yield make_user(council, username="srv-admin", password="OldPassw0rd-Long!")


def _login(client, email, password):
    r = client.post(LOGIN, {"email": email, "password": password}, format="json")
    assert r.status_code == 200, r.content
    return r.json()


@pytest.mark.django_db(transaction=True)
def test_password_change_revokes_other_sessions_refresh_tokens(account, api_client):
    attacker_session = _login(api_client, account.email, "OldPassw0rd-Long!")
    own_session = _login(api_client, account.email, "OldPassw0rd-Long!")

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {own_session['access']}")
    r = api_client.post(
        CHANGE, {"current_password": "OldPassw0rd-Long!", "new_password": "BrandNew-Passw0rd-42"}, format="json",
    )
    assert r.status_code == 204, r.content
    api_client.credentials()

    # Both pre-change refresh tokens are dead — the attacker's can no longer renew.
    assert api_client.post(REFRESH, {"refresh": attacker_session["refresh"]}, format="json").status_code == 401
    assert api_client.post(REFRESH, {"refresh": own_session["refresh"]}, format="json").status_code == 401

    # A fresh login with the new password works and yields a usable refresh token.
    fresh = _login(api_client, account.email, "BrandNew-Passw0rd-42")
    assert api_client.post(REFRESH, {"refresh": fresh["refresh"]}, format="json").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_failed_password_change_leaves_sessions_alone(account, api_client):
    session = _login(api_client, account.email, "OldPassw0rd-Long!")
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {session['access']}")
    r = api_client.post(CHANGE, {"current_password": "wrong", "new_password": "BrandNew-Passw0rd-42"}, format="json")
    assert r.status_code == 400
    api_client.credentials()
    assert api_client.post(REFRESH, {"refresh": session["refresh"]}, format="json").status_code == 200
