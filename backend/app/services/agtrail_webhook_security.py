import hashlib
import hmac

from app.core.config import settings


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Checks that an incoming webhook request was actually signed by AgTrail
    using our shared secret, and hasn't been altered in transit.

    AgTrail is expected to send a header (X-AgTrail-Signature) containing
    an HMAC-SHA256 signature of the raw request body, generated with the
    shared secret both sides agree on ahead of time.
    """
    if not signature_header:
        return False

    if not settings.AGTRAIL_WEBHOOK_SECRET:
        # No secret configured yet, this should only happen in local
        # development before AgTrail's real secret has been set.
        return False

    expected_signature = hmac.new(
        key=settings.AGTRAIL_WEBHOOK_SECRET.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # compare_digest prevents timing attacks, never compare signatures
    # with a plain == check.
    # Compared as bytes, a str containing non-ASCII characters would make
    # compare_digest raise instead of returning False.
    return hmac.compare_digest(
        expected_signature.encode("utf-8"),
        signature_header.strip().encode("utf-8"),
    )