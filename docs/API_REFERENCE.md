# ACRev360 API Reference

The hand-maintained endpoint-by-endpoint listing that used to live in this file is
retired — it could (and did) drift from the actual routes. The real, always-current
reference is generated straight from the DRF serializers/viewsets:

| What | Where |
|---|---|
| Interactive docs (Swagger UI) | `/api/docs/` |
| Interactive docs (Redoc) | `/api/redoc/` |
| Raw OpenAPI 3 schema | `/api/schema/` |
| Base URL (local dev) | `http://127.0.0.1:8000` |

## Authentication

`POST /api/v1/auth/login` exchanges `{"username", "password"}` for a JWT pair
(`access`, `refresh`). Send `Authorization: Bearer <access>` on every subsequent
request. `POST /api/v1/auth/refresh` rotates the pair; `POST /api/v1/auth/logout`
blacklists the refresh token. Access tokens are short-lived (30 min); see
`config/settings/base.py`'s `SIMPLE_JWT` block for exact lifetimes.

A handful of endpoints are public (no token required) by design — bill lookup by
reference, receipt verification by QR token, the channel catalogue, channel
webhooks (HMAC-signature-verified instead), and `/api/v1/health`. Everything else
requires a token and is scoped by the caller's council (Postgres row-level
security, V2_ARCHITECTURE.md §3) and, for `CONSULTANT` users, their own portfolio.

Because bill references are sequential, the public **bill lookup** returns the payer's
identity **masked** to anonymous callers (`pii_masked: true` — name as `A*** B***`,
phone/payer ref reduced to their last 3 characters, address blank); a signed-in staff
member of the bill's council (within their own portfolio, for consultants/agents) gets
the full record with `pii_masked: false`. The receipt-verification token is an
unguessable UUID, so possession of it is the credential.

## Rate limits

Every endpoint is throttled per caller and answers `429` with a `Retry-After` header
and `{"error": "Request was throttled. Expected available in N seconds."}` when a limit
is hit. Defaults (all overridable by environment variable, see
`config/settings/base.py`): login 10/min and 60/hour per submitted email; 600/min per
signed-in user; 120/min per IP for anonymous requests; 30/min per IP for the public
bill and receipt lookups; 1200/min for the USSD gateway; 600/min for channel webhooks.
`/api/v1/health` is never throttled. IP-based limits need `NUM_PROXIES` set correctly
in production — `docs/DEPLOYMENT.md` §8.

## Error format

Non-field errors come back as `{"error": "..."}`. Field-level validation errors
keep DRF's native shape (`{"phone": ["This field is required."]}`) — see
`apps/common/exceptions.py`.

## Generating a typed client / Postman collection

The OpenAPI schema at `/api/schema/` is what the frontend's typed API client
generates from, and what a Postman/Insomnia import should point at directly
(`Import > Link`, paste the schema URL) rather than a checked-in static export —
so the collection can never fall out of sync with the actual API either.
