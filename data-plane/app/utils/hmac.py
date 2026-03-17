import hashlib
import hmac
import time


def compute_signature(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    body: bytes,
) -> str:
    """Compute HMAC-SHA256 signature over request components.

    Signature = HMAC-SHA256(secret, METHOD + path + timestamp + SHA256(body))
    """
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{method.upper()}\n{path}\n{timestamp}\n{body_hash}"
    return hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    body: bytes,
    signature: str,
    max_age: int = 300,
) -> tuple[bool, str | None]:
    """Verify HMAC-SHA256 signature.

    Returns (is_valid, error_message).
    """
    # Check timestamp freshness
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False, "Invalid timestamp"

    age = abs(time.time() - ts)
    if age > max_age:
        return False, f"Request expired ({int(age)}s old, max {max_age}s)"

    expected = compute_signature(secret, method, path, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        return False, "Invalid signature"

    return True, None
