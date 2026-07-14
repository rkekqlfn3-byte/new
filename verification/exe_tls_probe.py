"""Verify certificate enforcement inside the packaged JARVIS runtime."""

import json
import ssl
import sys
import urllib.error
from pathlib import Path

from engine.network import (
    get_verified_ssl_context,
    is_certificate_verification_error,
    urlopen_verified,
)


def main():
    output = Path(sys.argv[1]).resolve()
    context = get_verified_ssl_context()
    default_context = ssl._create_default_https_context()
    results = {
        "verify_mode_cert_required": context.verify_mode == ssl.CERT_REQUIRED,
        "hostname_check_enabled": context.check_hostname is True,
        "default_context_cert_required": (
            default_context.verify_mode == ssl.CERT_REQUIRED
        ),
        "default_context_hostname_check_enabled": default_context.check_hostname is True,
        "trusted_ca_count": context.cert_store_stats().get("x509_ca", 0),
    }

    try:
        urlopen_verified("https://api.openai.com/v1/models", timeout=10)
        results["valid_certificate_handshake"] = True
    except urllib.error.HTTPError as error:
        # An authenticated API response is not required; HTTP status proves the
        # verified TLS handshake completed before the server rejected the call.
        results["valid_certificate_handshake"] = error.code in {
            400, 401, 403, 404, 405,
        }
        results["valid_http_status"] = error.code
    except Exception as error:
        results["valid_certificate_handshake"] = False
        results["valid_error"] = type(error).__name__

    try:
        urlopen_verified("https://self-signed.badssl.com/", timeout=10)
        results["self_signed_certificate_blocked"] = False
    except Exception as error:
        results["self_signed_certificate_blocked"] = (
            is_certificate_verification_error(error)
        )
        results["self_signed_error"] = type(
            getattr(error, "reason", error)
        ).__name__

    required = (
        "verify_mode_cert_required",
        "hostname_check_enabled",
        "default_context_cert_required",
        "default_context_hostname_check_enabled",
        "valid_certificate_handshake",
        "self_signed_certificate_blocked",
    )
    results["all_passed"] = (
        results["trusted_ca_count"] > 0
        and all(results.get(name) is True for name in required)
    )
    output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not results["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
