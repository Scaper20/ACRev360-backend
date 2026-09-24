"""
Client-address helpers shared by the throttles, the audit trail and the admin
login guard.

Behind Cloudflare + Render's proxies the address that matters is a *position*
in X-Forwarded-For, not the whole header: DRF's ``NUM_PROXIES`` setting says how
many trusted hops are appended on the right, and ``get_ident`` then picks the
entry the outermost trusted proxy added — the one a client can't forge. With
NUM_PROXIES unset DRF keys on the entire header, which an attacker rotates for
free (see apps/accounts/throttles.py for the confirmed bypass). Set the
``NUM_PROXIES`` environment variable once the production hop count is verified
(docs/DEPLOYMENT.md, "Client IP behind the proxies").
"""
import ipaddress

from rest_framework.throttling import BaseThrottle


def client_ip(request) -> str | None:
    """The caller's address as DRF's throttles see it, or None when what comes
    back isn't a single valid IP (e.g. NUM_PROXIES unset and the header carries
    a chain) — safe to store in a GenericIPAddressField either way."""
    ident = BaseThrottle().get_ident(request)
    try:
        return str(ipaddress.ip_address(ident))
    except ValueError:
        return None
