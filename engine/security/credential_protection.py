"""Current-user protection for locally persisted API credentials."""

from __future__ import annotations

import base64
from typing import Protocol


class CredentialProtectionError(RuntimeError):
    """The operating-system credential boundary is unavailable or invalid."""


class CredentialProtector(Protocol):
    def protect(self, secret: str) -> str: ...

    def unprotect(self, protected: str) -> str: ...


class WindowsDpapiCredentialProtector:
    """Protect a secret for the current Windows user with DPAPI."""

    PREFIX = "dpapi:v1:"
    _ENTROPY = b"Jarvis.ai_config.v1"

    @staticmethod
    def _win32crypt():
        try:
            import win32crypt
        except ImportError as error:
            raise CredentialProtectionError(
                "Windows credential protection is unavailable."
            ) from error
        return win32crypt

    def protect(self, secret: str) -> str:
        value = str(secret or "")
        if not value:
            return ""
        try:
            encrypted = self._win32crypt().CryptProtectData(
                value.encode("utf-8"),
                "Jarvis AI API key",
                self._ENTROPY,
                None,
                None,
                0,
            )
        except Exception as error:
            raise CredentialProtectionError(
                "Windows could not protect the API credential."
            ) from error
        return self.PREFIX + base64.b64encode(encrypted).decode("ascii")

    def unprotect(self, protected: str) -> str:
        payload = str(protected or "")
        if not payload:
            return ""
        if not payload.startswith(self.PREFIX):
            raise CredentialProtectionError("Unknown credential protection format.")
        try:
            encrypted = base64.b64decode(
                payload[len(self.PREFIX):], validate=True
            )
            _description, decrypted = self._win32crypt().CryptUnprotectData(
                encrypted,
                self._ENTROPY,
                None,
                None,
                0,
            )
            return decrypted.decode("utf-8")
        except CredentialProtectionError:
            raise
        except Exception as error:
            raise CredentialProtectionError(
                "Windows could not read the protected API credential."
            ) from error


def default_credential_protector() -> CredentialProtector:
    return WindowsDpapiCredentialProtector()
