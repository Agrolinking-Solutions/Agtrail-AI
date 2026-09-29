import hmac

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from app.core.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """
    Rejects the request unless it carries one of the keys in API_KEYS.
    Returns the label of the matching key, so callers can record who
    made a change.
    """
    if api_key:
        # Check every key rather than stopping at the first match, and use
        # compare_digest, so response time doesn't reveal key contents.
        matched_label = None
        for key, label in settings.api_keys.items():
            if hmac.compare_digest(key.encode("utf-8"), api_key.encode("utf-8")):
                matched_label = label
        if matched_label is not None:
            return matched_label

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "ApiKey"},
    )
