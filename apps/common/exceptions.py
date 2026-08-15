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
    if isinstance(response.data, dict) and set(response.data) == {"detail"}:
        response.data = {"error": str(response.data["detail"])}
    return response
