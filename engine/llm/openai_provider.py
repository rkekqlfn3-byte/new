import json
import logging
import urllib.error
import urllib.request

from engine.llm.base import ProviderBase
from engine.network import (
    is_certificate_verification_error,
    tls_certificate_failure,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(ProviderBase):
    def call(
        self,
        api_key,
        prompt,
        user_input,
        image_data=None,
        mode="command",
        temperature=0.2,
        stream_callback=None,
        *,
        opener,
    ):
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "JarvisApp/1.0"
        }

        messages = [{"role": "system", "content": prompt}]

        if isinstance(user_input, list):
            for idx, msg in enumerate(user_input):
                role = "assistant" if msg.get("role") == "assistant" else "user"
                content = msg.get("content", "")

                if idx == len(user_input) - 1 and image_data and role == "user":
                    messages.append({
                        "role": role,
                        "content": [
                            {"type": "text", "text": content},
                            {"type": "image_url", "image_url": {"url": image_data}}
                        ]
                    })
                else:
                    messages.append({"role": role, "content": content})
        else:
            if image_data:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_input},
                        {"type": "image_url", "image_url": {"url": image_data}}
                    ]
                })
            else:
                messages.append({"role": "user", "content": user_input})

        data = {
            "model": "gpt-4o-mini",
            "messages": messages,
            "temperature": temperature
        }

        if mode in ["conversation", "question"] and stream_callback:
            data["stream"] = True

        if mode == "command":
            data["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "jarvis_command_response",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "response": {"type": "string"},
                            "actions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "action": {"type": "string", "enum": ["open_app", "action_plan", "dynamic_code", "use_learned_macro", "adapted_macro", "read_and_analyze", "none"]},
                                        "target": {"type": "string"},
                                        "app_name": {"type": "string"},
                                        "macro_name": {"type": "string"},
                                        "description": {"type": "string"},
                                        "code": {"type": "string"},
                                        "explanation_steps": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "step": {"type": "string"},
                                                    "code_snippet": {"type": "string"}
                                                },
                                                "required": ["step", "code_snippet"],
                                                "additionalProperties": False
                                            }
                                        },
                                        "learning": {
                                            "type": "object",
                                            "properties": {
                                                "intent": {"type": "string"},
                                                "argument_mode": {"type": "string", "enum": ["json"]},
                                                "verbs": {"type": "array", "items": {"type": "string"}},
                                                "nouns": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "text": {"type": "string"},
                                                            "canonical": {"type": "string"},
                                                            "type": {"type": "string", "enum": ["app", "website", "file", "folder", "value", "general"]}
                                                        },
                                                        "required": ["text", "canonical", "type"],
                                                        "additionalProperties": False
                                                    }
                                                },
                                                "utterances": {"type": "array", "items": {"type": "string"}},
                                                "slots": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "name": {"type": "string"},
                                                            "type": {"type": "string", "enum": ["app", "text", "number", "color", "direction", "path", "url", "cell", "value"]},
                                                            "value": {"type": "string"},
                                                            "required": {"type": "boolean"}
                                                        },
                                                        "required": ["name", "type", "value", "required"],
                                                        "additionalProperties": False
                                                    }
                                                }
                                            },
                                            "required": ["intent", "argument_mode", "verbs", "nouns", "utterances", "slots"],
                                            "additionalProperties": False
                                        },
                                        "plan": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "action": {"type": "string", "enum": ["open_app", "focus_window", "move_window", "window_state", "hotkey", "type_text", "wait", "navigate_url", "clipboard_set", "clipboard_get", "copy_file", "move_file", "create_folder", "write_text_file", "uia_click", "uia_set_text", "uia_select_file"]},
                                                    "target": {"type": "string"},
                                                    "direction": {"type": "string"},
                                                    "selector": {
                                                        "type": "object",
                                                        "properties": {
                                                            "automation_id": {"type": "string"},
                                                            "name": {"type": "string"},
                                                            "control_type": {"type": "string"},
                                                            "parent_name": {"type": "string"},
                                                            "ancestor_name": {"type": "string"},
                                                            "match_mode": {"type": "string", "enum": ["auto", "exact", "normalized", "contains"]}
                                                        },
                                                        "required": ["automation_id", "name", "control_type", "parent_name", "ancestor_name", "match_mode"],
                                                        "additionalProperties": False
                                                    },
                                                    "keys": {"type": "array", "items": {"type": "string"}},
                                                    "text": {"type": "string"},
                                                    "seconds": {"type": "number"},
                                                    "x": {"type": "integer"},
                                                    "y": {"type": "integer"},
                                                    "width": {"type": "integer"},
                                                    "height": {"type": "integer"}
                                                    ,"overwrite": {"type": "boolean"}
                                                },
                                                "required": ["action", "target", "direction", "selector", "keys", "text", "seconds", "x", "y", "width", "height", "overwrite"],
                                                "additionalProperties": False
                                            }
                                        }
                                    },
                                    "required": ["action", "target", "app_name", "macro_name", "description", "code", "explanation_steps", "learning", "plan"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["response", "actions"],
                        "additionalProperties": False
                    },
                    "strict": True
                }
            }
        elif mode == "json":
            data["response_format"] = {"type": "json_object"}

        req = urllib.request.Request(url, json.dumps(data).encode("utf-8"), headers)
        try:
            with opener(req, timeout=self.timeout) as response:
                if data.get("stream"):
                    full_content = ""
                    for line in response:
                        decoded_line = line.decode('utf-8').strip()
                        if decoded_line.startswith("data: "):
                            data_str = decoded_line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                                delta = chunk["choices"][0].get("delta", {})
                                text_chunk = delta.get("content", "")
                                if text_chunk:
                                    full_content += text_chunk
                                    stream_callback(text_chunk)
                            except (
                                UnicodeDecodeError,
                                json.JSONDecodeError,
                                KeyError,
                                IndexError,
                                TypeError,
                                AttributeError,
                            ):
                                logger.debug(
                                    "Skipping malformed OpenAI stream chunk",
                                    exc_info=True,
                                )
                    return {"response": full_content, "action": "none", "target": None}
                else:
                    result = json.loads(response.read().decode("utf-8"))
                    content = result["choices"][0]["message"]["content"]

                    if mode == "command":
                        return self.decode_command_content(content)
                    else:
                        return {"response": content, "action": "none", "target": None}
        except urllib.error.HTTPError as e:
            return {"response": f"앗! OpenAI API 에러! ({e.code})", "action": "none", "target": None, "provider_error": True, "provider": "openai"}
        except Exception as error:
            if is_certificate_verification_error(error):
                return tls_certificate_failure("openai")
            logger.exception("OpenAI request failed")
            return {"response": "앗! OpenAI 연결에 실패했어 ㅠㅠ 로그를 확인해줘.", "action": "none", "target": None, "provider_error": True, "provider": "openai"}
