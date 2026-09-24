"""
Version tokens for cached read models (dashboard totals, the revenue-item
catalogue).

A cached payload's key embeds the current token for the data it was built from;
any write to that data replaces the token (apps/common/signals.py), so the old
payload is simply never looked up again — no scanning or deleting of keys, and
a write can never leave a stale entry behind in the process that made it.

Tokens are random rather than counters on purpose: a counter restarts at zero
when the process does (Render's free tier spins down after 15 idle minutes), and
a browser holding ETag "…-3" from before the restart would then be told
"unchanged" about completely different data. A fresh random token after a
restart can only ever cause a harmless refetch.

The default cache is per-process (LocMemCache), which is right for the single
gunicorn worker the free tier runs: the process that handles a write is the
process that serves the next read. With several workers a write in one leaves
the others' tokens alone until they expire (TOKEN_TTL) — bounded staleness, not
an incorrect answer — and pointing CACHES at a shared backend removes even that
(see docs/DEPLOYMENT.md, "Scaling past one worker").
"""
import hashlib
import uuid

from django.core.cache import cache

#: How long a token — and therefore any payload built under it — may live with
#: no write to refresh it. The ceiling on staleness when a write happened in a
#: different process or bypassed the ORM (queryset.update, the nightly debt-
#: ageing job), and the reason nothing here needs manual expiry.
TOKEN_TTL = 120

#: Per-namespace override: the catalogue is edited a few times a month, so it
#: can safely live longer between writes than the dashboard totals.
_TTL = {"catalogue": 600}


def _ttl(namespace: str) -> int:
    return _TTL.get(namespace, TOKEN_TTL)


def _key(namespace: str, council_id) -> str:
    return f"ver:{namespace}:{council_id if council_id is not None else 'all'}"


def version_token(namespace: str, council_id=None) -> str:
    """Current token for (namespace, council); minted on first use."""
    key = _key(namespace, council_id)
    token = cache.get(key)
    if token is None:
        token = uuid.uuid4().hex
        # add(), not set(): if another thread minted one in the meantime, use it.
        if not cache.add(key, token, _ttl(namespace)):
            token = cache.get(key) or token
    return token


def bump(namespace: str, council_id=None) -> None:
    """Invalidate everything cached under (namespace, council)."""
    cache.set(_key(namespace, council_id), uuid.uuid4().hex, _ttl(namespace))


def digest(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def cached(namespace: str, council_id, parts, compute, ttl: int | None = None):
    """Return ``compute()``'s result, memoised under the current token for
    (namespace, council) and the caller-specific ``parts`` (anything that changes
    what ``compute`` returns — role, portfolio, query string)."""
    key = f"data:{namespace}:{council_id}:{version_token(namespace, council_id)}:{digest(*parts)}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = compute()
    cache.set(key, value, ttl or _ttl(namespace))
    return value
