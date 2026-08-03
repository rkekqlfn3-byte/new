"""TLS helpers that always require a trusted certificate and hostname match."""

from __future__ import annotations

import ssl
import urllib.request
from functools import lru_cache

TLS_CERTIFICATE_MESSAGE = (
    "보안 인증서 검증에 실패했습니다. 시스템 날짜·시간과 Windows 루트 "
    "인증서를 확인해주세요. 안전을 위해 인증서 검증을 끄지 않았습니다."
)


@lru_cache(maxsize=1)
def get_verified_ssl_context():
    """Return the shared client context backed by the OS default trust store."""
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    # Keep these explicit so a future refactor cannot silently weaken them.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def urlopen_verified(request, timeout=60):
    """Open an HTTPS request without any insecure fallback path."""
    return urllib.request.urlopen(
        request,
        timeout=timeout,
        context=get_verified_ssl_context(),
    )


def is_certificate_verification_error(error):
    """Recognize direct and urllib-wrapped certificate verification failures."""
    queue = [error]
    seen = set()
    while queue and len(seen) < 12:
        current = queue.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if (
            isinstance(current, ssl.SSLError)
            and "CERTIFICATE_VERIFY_FAILED" in str(current).upper()
        ):
            return True
        for attribute in ("reason", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                queue.append(nested)
    return False


def tls_certificate_failure(provider):
    """Return the provider result used for a blocked certificate connection."""
    return {
        "response": TLS_CERTIFICATE_MESSAGE,
        "action": "none",
        "target": None,
        "provider_error": True,
        "provider": str(provider or "cloud"),
        "tls_certificate_error": True,
        "error_type": "tls_certificate_error",
        "retryable": False,
    }
