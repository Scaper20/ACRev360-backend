"""
PR 8: authenticate by email instead of username. USERNAME_FIELD stays
"username" (lower blast radius — Django admin/permissions internals key off
it too); only the JWT login serializer's lookup changed. Every account
without an explicit email (every onboarding flow today) gets a unique
placeholder assigned automatically via AppUserManager, so making email
unique+required at the model level doesn't break anyone.
"""
import pytest
from django.db import IntegrityError, transaction

from apps.accounts.models import AppUser
from apps.tenancy.context import set_council_context


@pytest.fixture
def scoped(make_council, make_user):
    council = make_council(code="ELG")
    with transaction.atomic():
        set_council_context(council.id)
        admin = make_user(council, username="elg-admin", password="testpass12345")
        yield {"council": council, "admin": admin}


@pytest.mark.django_db(transaction=True)
def test_login_with_email_succeeds(scoped, api_client):
    admin = scoped["admin"]
    r = api_client.post("/api/v1/auth/login", {"email": admin.email, "password": "testpass12345"}, format="json")
    assert r.status_code == 200, r.content
    assert "access" in r.json()
    assert "refresh" in r.json()


@pytest.mark.django_db(transaction=True)
def test_login_with_email_is_case_insensitive(scoped, api_client):
    admin = scoped["admin"]
    r = api_client.post(
        "/api/v1/auth/login", {"email": admin.email.upper(), "password": "testpass12345"}, format="json",
    )
    assert r.status_code == 200, r.content


@pytest.mark.django_db(transaction=True)
def test_login_with_wrong_password_rejected(scoped, api_client):
    admin = scoped["admin"]
    r = api_client.post("/api/v1/auth/login", {"email": admin.email, "password": "wrong-password"}, format="json")
    assert r.status_code == 401, r.content


@pytest.mark.django_db(transaction=True)
def test_login_with_unknown_email_rejected(scoped, api_client):
    r = api_client.post(
        "/api/v1/auth/login", {"email": "nobody@nowhere.example", "password": "testpass12345"}, format="json",
    )
    assert r.status_code == 401, r.content


@pytest.mark.django_db(transaction=True)
def test_login_with_username_no_longer_works(scoped, api_client):
    """The old contract (username + password) is gone — email is required
    now, matching the new login serializer's own field."""
    admin = scoped["admin"]
    r = api_client.post(
        "/api/v1/auth/login", {"username": admin.username, "password": "testpass12345"}, format="json",
    )
    assert r.status_code == 400, r.content


@pytest.mark.django_db(transaction=True)
def test_new_users_get_unique_placeholder_emails_when_not_provided(scoped, make_user):
    user_a = make_user(scoped["council"], username="elg-a")
    user_b = make_user(scoped["council"], username="elg-b")
    assert user_a.email == "elg-a@placeholder.acrev360.local"
    assert user_b.email == "elg-b@placeholder.acrev360.local"
    assert user_a.email != user_b.email


@pytest.mark.django_db(transaction=True)
def test_email_uniqueness_enforced_at_model_level(scoped):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            AppUser.objects.create_user(username="elg-dupe", password="x", full_name="Dupe", email=scoped["admin"].email)


@pytest.mark.django_db(transaction=True)
def test_backfill_migration_gave_every_blank_email_account_a_placeholder(scoped):
    """Guards the actual PR8 acceptance criterion at the data level: no
    active account should be able to have a blank email post-migration."""
    assert not AppUser.objects.filter(email="").exists()
