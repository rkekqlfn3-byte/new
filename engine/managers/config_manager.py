import threading


class ConfigManager:
    ROUTING_MODES = {"auto", "local_only", "ai_first"}
    def __init__(self, ai_config, save_callback, lock=None):
        self.ai_config = ai_config
        self.save = save_callback
        self.lock = lock or threading.RLock()

    def get_ai_config(self):
        with self.lock:
            return dict(self.ai_config)

    def save_ai_config(self, provider, api_key, ollama_model="llama3", routing_mode="auto"):
        with self.lock:
            self.ai_config["provider"] = provider
            self.ai_config["api_key"] = api_key
            self.ai_config["ollama_model"] = ollama_model
            self.ai_config["routing_mode"] = (
                routing_mode if routing_mode in self.ROUTING_MODES else "auto"
            )
            self.save()
            return True
