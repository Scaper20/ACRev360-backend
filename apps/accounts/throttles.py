"""
Login brute-force throttles keyed on the *submitted email*, not the client
address.

DRF's built-in scoped throttle identifies anonymous callers by IP — and with no
NUM_PROXIES configured it keys on the whole X-Forwarded-For header, which a
client controls. Confirmed both ways: locally, rotating a spoofed
X-Forwarded-For bypassed the 10/min login limit completely (14 of 14 attempts
allowed); against production, behind Cloudflare + Render's proxy chain, 14
consecutive bad logins never produced a single 429 even without spoofing. An
attacker who wants one specific account (the only interesting target — emails
here are guessable) shouldn't get to choose how many guesses they receive, so
these count attempts against the account itself, whatever address they come
from. The IP-scoped throttle stays in place as an extra, best-effort layer.

Trade-off, accepted: someone can burn a victim's budget to make their login
429 for up to a minute (burst) or hour (sustained). That is a nuisance, not a
takeover, and the alternative — unlimited guessing — isn't.

Counters live in Django's cache (LocMemCache today: per-process, fine for the
single gunicorn worker Render runs; move CACHES to a shared backend before
running more than one).
"""
import hashlib

from rest_framework.throttling import SimpleRateThrottle


class _LoginEmailThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        data = request.data
        email = data.get("email") if hasattr(data, "get") else None
        email = str(email or "").strip().lower()
        if not email:
            return None  # nothing to key on — the IP-scoped throttle still applies
        return self.cache_format % {"scope": self.scope, "ident": hashlib.sha256(email.encode()).hexdigest()}


class LoginEmailBurstThrottle(_LoginEmailThrottle):
    """Short window — stops rapid-fire guessing."""

    scope = "login_email_burst"


class LoginEmailSustainedThrottle(_LoginEmailThrottle):
    """Long window — caps slow-and-low guessing that stays under the burst limit."""

    scope = "login_email_sustained"
