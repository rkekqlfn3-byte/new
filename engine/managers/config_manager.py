import threading

from engine.security.credential_protection import CredentialProtector


class ConfigManager:
    PROVIDERS = {"openai", "gemini"}
    ROUTING_MODES = {"auto", "local_only", "ai_first"}
    def __init__(
        self,
        ai_config,
        save_callback,
        lock=None,
        *,
        credential_protector: CredentialProtector,
        protected_api_key="",
        credential_error="",
    ):
        self.ai_config = ai_config
        self.save = save_callback
        self.lock = lock or threading.RLock()
        self.credential_protector = credential_protector
        self.protected_api_key = str(protected_api_key or "")
        self.credential_error = str(credential_error or "")

    def get_ai_config(self):
        with self.lock:
            return dict(self.ai_config)

    def get_public_ai_config(self):
        """Return GUI-safe settings without copying the stored secret."""
        with self.lock:
            return {
                "provider": self.ai_config.get("provider", "openai"),
                "routing_mode": self.ai_config.get("routing_mode", "auto"),
                "has_api_key": bool(str(self.ai_config.get("api_key", "")).strip()),
                "credential_status": (
                    "unavailable" if self.credential_error
                    else "ready" if self.ai_config.get("api_key")
                    else "empty"
                ),
            }

    def serialized_config(self):
        with self.lock:
            return {
                "provider": self.ai_config.get("provider", "openai"),
                "api_key_protected": self.protected_api_key,
                "routing_mode": self.ai_config.get("routing_mode", "auto"),
            }

    def replace_loaded_config(
        self, ai_config, protected_api_key="", credential_error=""
    ):
        with self.lock:
            self.ai_config = ai_config
            self.protected_api_key = str(protected_api_key or "")
            self.credential_error = str(credential_error or "")

    def save_ai_config(self, provider, api_key, routing_mode="auto"):
        with self.lock:
            previous = dict(self.ai_config)
            previous_protected = self.protected_api_key
            previous_error = self.credential_error
            new_api_key = str(api_key or "").strip()
            protected = (
                self.credential_protector.protect(new_api_key)
                if new_api_key else ""
            )
            provider = str(provider or "openai").strip().casefold()
            self.ai_config["provider"] = (
                provider if provider in self.PROVIDERS else "openai"
            )
            # An empty GUI field means "keep the existing key". Secret
            # deletion is deliberately separated into an explicit action.
            if new_api_key:
                self.ai_config["api_key"] = new_api_key
                self.protected_api_key = protected
                self.credential_error = ""
            self.ai_config["routing_mode"] = (
                routing_mode if routing_mode in self.ROUTING_MODES else "auto"
            )
            try:
                self.save()
            except (OSError, TypeError, ValueError):
                self.ai_config.clear()
                self.ai_config.update(previous)
                self.protected_api_key = previous_protected
                self.credential_error = previous_error
                raise
            return True

    def clear_ai_api_key(self):
        with self.lock:
            previous = dict(self.ai_config)
            previous_protected = self.protected_api_key
            previous_error = self.credential_error
            self.ai_config["api_key"] = ""
            self.protected_api_key = ""
            self.credential_error = ""
            try:
                self.save()
            except (OSError, TypeError, ValueError):
                self.ai_config.clear()
                self.ai_config.update(previous)
                self.protected_api_key = previous_protected
                self.credential_error = previous_error
                raise
            return True
