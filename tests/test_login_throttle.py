"""
Login brute-force budget. The IP-scoped throttle DRF gives us keys on the whole
X-Forwarded-For header when NUM_PROXIES is unset, so a client rotating that
header got a fresh budget every request (14 of 14 bad logins allowed —
reproduced locally), and behind Cloudflare + Render it never tripped at all in
production. The per-email throttles in apps/accounts/throttles.py hold no
matter what address or header the attempts arrive with.
"""
import pytest
from django.core.cache import cache
from django.db import transaction
from rest_framework.throttling import SimpleRateThrottle

from apps.tenancy.context import set_council_context

LOGIN = "/api/v1/auth/login"


@pytest.fixture(autouse=True)
def tight_login_budget(monkeypatch):
    # dev.py deliberately sets these to never-trip for the rest of the suite;
    # this file needs the real mechanism with a small, fast-to-exhaust budget.
    # The IP scope is left generous so only the per-email ones can be what trips.
    monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {
        "login": "1000/min", "login_email_burst": "3/min", "login_email_sustained": "100/hour",
    })
    cache.clear()
    yield
    cache.clear()


def _attempt(client, email, password="wrong-password", **extra):
    return client.post(LOGIN, {"email": email, "password": password}, format="json", **extra)


@pytest.mark.django_db(transaction=True)
def test_rotating_x_forwarded_for_cannot_bypass_the_per_email_limit(api_client):
    codes = [
        _attempt(api_client, "victim@example.com", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}").status_code
        for i in range(6)
    ]
    assert codes == [401, 401, 401, 429, 429, 429]


@pytest.mark.django_db(transaction=True)
def test_email_match_is_case_and_whitespace_insensitive(api_client):
    for variant in ("Victim@Example.com", " victim@example.com ", "VICTIM@EXAMPLE.COM"):
        assert _attempt(api_client, variant).status_code == 401
    assert _attempt(api_client, "victim@example.com").status_code == 429


@pytest.mark.django_db(transaction=True)
def test_one_emails_budget_does_not_affect_another(api_client):
    for _ in range(5):
        _attempt(api_client, "victim@example.com")
    assert _attempt(api_client, "victim@example.com").status_code == 429
    assert _attempt(api_client, "someone-else@example.com").status_code == 401


@pytest.mark.django_db(transaction=True)
def test_legitimate_login_still_works_within_budget(make_council, make_user, api_client):
    council = make_council(code="THR")
    with transaction.atomic():
        set_council_context(council.id)
        user = make_user(council, username="thr-admin", password="testpass12345")
    assert _attempt(api_client, user.email, password="testpass12345").status_code == 200


@pytest.mark.django_db(transaction=True)
def test_throttled_response_uses_the_normalised_error_shape(api_client):
    for _ in range(3):
        _attempt(api_client, "victim@example.com")
    r = _attempt(api_client, "victim@example.com")
    assert r.status_code == 429
    assert "error" in r.json()
