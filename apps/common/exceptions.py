from rest_framework.views import exception_handler


def acrev360_exception_handler(exc, context):
    """
    Normalises DRF's default `{"detail": "..."}` (401/403/404/405/429/500) into
    `{"error": "..."}` for a single, predictable top-level error shape. Field-level
    validation errors (`{"phone": ["This field is required."]}`) are left as-is —
    that's more useful to the frontend than flattening them.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None
    # SimpleJWT's auth-failure responses (and some other DRF exceptions) add a
    # sibling `code` key alongside `detail` — `{"detail": "...", "code": "..."}
    # — so this used to only fire when `detail` was the *only* key, silently
    # leaking that two-key shape straight to the frontend, which doesn't know
    # how to read it and fell back to a generic "Something went wrong" on any
    # failed login. Normalise whenever `detail` is present, regardless of
    # whatever else rides along with it.
    if isinstance(response.data, dict) and "detail" in response.data:
        normalised = {"error": str(response.data["detail"])}
        # Keep a `code` sibling when present (SimpleJWT adds codes like
        # "no_active_account"; login also surfaces must_change_password flows)
        # so the frontend can branch on it without string-matching the error.
        if response.data.get("code"):
            normalised["code"] = response.data["code"]
        response.data = normalised
    return response
