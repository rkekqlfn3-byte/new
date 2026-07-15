import threading


class ConfigManager:
    PROVIDERS = {"openai", "gemini"}
    ROUTING_MODES = {"auto", "local_only", "ai_first"}
    def __init__(self, ai_config, save_callback, lock=None):
        self.ai_config = ai_config
        self.save = save_callback
        self.lock = lock or threading.RLock()

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
            }

    def save_ai_config(self, provider, api_key, routing_mode="auto"):
        with self.lock:
            previous = dict(self.ai_config)
            provider = str(provider or "openai").strip().casefold()
            self.ai_config["provider"] = (
                provider if provider in self.PROVIDERS else "openai"
            )
            # An empty GUI field means "keep the existing key". Secret
            # deletion is deliberately separated into an explicit action.
            new_api_key = str(api_key or "").strip()
            if new_api_key:
                self.ai_config["api_key"] = new_api_key
            self.ai_config["routing_mode"] = (
                routing_mode if routing_mode in self.ROUTING_MODES else "auto"
            )
            try:
                self.save()
            except (OSError, TypeError, ValueError):
                self.ai_config.clear()
                self.ai_config.update(previous)
                raise
            return True

    def clear_ai_api_key(self):
        with self.lock:
            previous = dict(self.ai_config)
            self.ai_config["api_key"] = ""
            try:
                self.save()
            except (OSError, TypeError, ValueError):
                self.ai_config.clear()
                self.ai_config.update(previous)
                raise
            return True
