import json
import logging
import time
import urllib.error
import urllib.request

from engine.llm.base import ProviderBase
from engine.network import (
    is_certificate_verification_error,
    tls_certificate_failure,
)

logger = logging.getLogger(__name__)


class GeminiProvider(ProviderBase):
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
        is_stream = (mode in ["conversation", "question"] and stream_callback)
        if is_stream:
            url_method = "streamGenerateContent?alt=sse"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:{url_method}&key={api_key}"
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "JarvisApp/1.0"
        }

        contents = []
        if isinstance(user_input, list):
            for idx, msg in enumerate(user_input):
                role = "model" if msg.get("role") == "assistant" else "user"
                content = msg.get("content", "")
                parts = [{"text": content}]

                if idx == len(user_input) - 1 and image_data and role == "user":
                    try:
                        mime_type = image_data.split(';')[0].split(':')[1]
                        base64_str = image_data.split(',')[1]
                        parts.append({"inlineData": {"mimeType": mime_type, "data": base64_str}})
                    except (AttributeError, IndexError, TypeError):
                        logger.debug("Skipping malformed Gemini image data", exc_info=True)
                contents.append({"role": role, "parts": parts})

            if len(contents) > 0 and contents[0]["role"] == "user":
                prefix = "[시스템 지시]\n" if mode in ["conversation", "question"] else "사용자 명령: "
                contents[0]["parts"][0]["text"] = f"{prompt}\n\n{prefix}" + contents[0]["parts"][0]["text"]
            else:
                contents.insert(0, {"role": "user", "parts": [{"text": prompt}]})
        else:
            prefix = "[시스템 지시]\n" if mode in ["conversation", "question"] else "사용자 명령: "
            parts = [{"text": f"{prompt}\n\n{prefix}{user_input}"}]
            if image_data:
                try:
                    mime_type = image_data.split(';')[0].split(':')[1]
                    base64_str = image_data.split(',')[1]
                    parts.append({"inlineData": {"mimeType": mime_type, "data": base64_str}})
                except (AttributeError, IndexError, TypeError):
                    logger.debug("Skipping malformed Gemini image data", exc_info=True)
            contents = [{"role": "user", "parts": parts}]

        data = {
            "contents": contents,
            "safetySettings": [
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE"
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 8192
            }
        }

        if mode == "command":
            data["generationConfig"]["responseMimeType"] = "application/json"
            data["generationConfig"]["responseSchema"] = {
                "type": "OBJECT",
                "properties": {
                    "response": {"type": "STRING"},
                    "actions": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "action": {"type": "STRING", "enum": ["open_app", "action_plan", "dynamic_code", "use_learned_macro", "adapted_macro", "read_and_analyze", "none"]},
                                "target": {"type": "STRING"},
                                "app_name": {"type": "STRING"},
                                "macro_name": {"type": "STRING"},
                                "description": {"type": "STRING"},
                                "code": {"type": "STRING"},
                                "explanation_steps": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "step": {"type": "STRING"},
                                            "code_snippet": {"type": "STRING"}
                                        }
                                    }
                                },
                                "learning": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "intent": {"type": "STRING"},
                                        "argument_mode": {"type": "STRING", "enum": ["json"]},
                                        "verbs": {"type": "ARRAY", "items": {"type": "STRING"}},
                                        "nouns": {
                                            "type": "ARRAY",
                                            "items": {
                                                "type": "OBJECT",
                                                "properties": {
                                                    "text": {"type": "STRING"},
                                                    "canonical": {"type": "STRING"},
                                                    "type": {"type": "STRING", "enum": ["app", "website", "file", "folder", "value", "general"]}
                                                },
                                                "required": ["text", "canonical", "type"]
                                            }
                                        },
                                        "utterances": {"type": "ARRAY", "items": {"type": "STRING"}},
                                        "slots": {
                                            "type": "ARRAY",
                                            "items": {
                                                "type": "OBJECT",
                                                "properties": {
                                                    "name": {"type": "STRING"},
                                                    "type": {"type": "STRING", "enum": ["app", "text", "number", "color", "direction", "path", "url", "cell", "value"]},
                                                    "value": {"type": "STRING"},
                                                    "required": {"type": "BOOLEAN"}
                                                },
                                                "required": ["name", "type", "value", "required"]
                                            }
                                        }
                                    },
                                    "required": ["intent", "argument_mode", "verbs", "nouns", "utterances", "slots"]
                                },
                                "plan": {
                                    "type": "ARRAY",
                                    "items": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "action": {"type": "STRING", "enum": ["open_app", "focus_window", "move_window", "window_state", "hotkey", "type_text", "wait", "navigate_url", "clipboard_set", "clipboard_get", "copy_file", "move_file", "create_folder", "write_text_file", "uia_click", "uia_set_text", "uia_select_file"]},
                                            "target": {"type": "STRING"},
                                            "direction": {"type": "STRING"},
                                            "selector": {
                                                "type": "OBJECT",
                                                "properties": {
                                                    "automation_id": {"type": "STRING"},
                                                    "name": {"type": "STRING"},
                                                    "control_type": {"type": "STRING"},
                                                    "parent_name": {"type": "STRING"},
                                                    "ancestor_name": {"type": "STRING"},
                                                    "match_mode": {"type": "STRING", "enum": ["auto", "exact", "normalized", "contains"]}
                                                },
                                                "required": ["automation_id", "name", "control_type", "parent_name", "ancestor_name", "match_mode"]
                                            },
                                            "keys": {"type": "ARRAY", "items": {"type": "STRING"}},
                                            "text": {"type": "STRING"},
                                            "seconds": {"type": "NUMBER"},
                                            "x": {"type": "INTEGER"},
                                            "y": {"type": "INTEGER"},
                                            "width": {"type": "INTEGER"},
                                            "height": {"type": "INTEGER"}
                                            ,"overwrite": {"type": "BOOLEAN"}
                                        },
                                        "required": ["action", "target", "direction", "selector", "keys", "text", "seconds", "x", "y", "width", "height", "overwrite"]
                                    }
                                }
                            },
                            "required": ["action", "target", "app_name", "macro_name", "description", "code", "explanation_steps", "learning", "plan"]
                        }
                    }
                },
                "required": ["response", "actions"]
            }
        elif mode == "json":
            data["generationConfig"]["responseMimeType"] = "application/json"

        req = urllib.request.Request(url, json.dumps(data).encode("utf-8"), headers)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                with opener(req, timeout=self.timeout) as response:
                    if is_stream:
                        full_content = ""
                        for line in response:
                            decoded_line = line.decode('utf-8').strip()
                            if decoded_line.startswith("data: "):
                                data_str = decoded_line[6:]
                                try:
                                    chunk = json.loads(data_str)
                                    text_chunk = chunk["candidates"][0]["content"]["parts"][0]["text"]
                                    if text_chunk:
                                        full_content += text_chunk
                                        stream_callback(text_chunk)
                                except (
                                    json.JSONDecodeError,
                                    KeyError,
                                    IndexError,
                                    TypeError,
                                    AttributeError,
                                ):
                                    logger.debug(
                                        "Skipping malformed Gemini stream chunk",
                                        exc_info=True,
                                    )
                        return {"response": full_content, "action": "none", "target": None}
                    else:
                        result = json.loads(response.read().decode("utf-8"))
                        content = result["candidates"][0]["content"]["parts"][0]["text"]
                        content = content.strip()

                        if mode == "command":
                            return self.decode_command_content(content)
                        else:
                            return {"response": content, "action": "none", "target": None}
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                if e.code in [503, 429] and attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                logger.warning(
                    "Gemini HTTP error status=%s body=%s", e.code, err_body[:500]
                )
                return {"response": f"Gemini API 에러! (상태: {e.code}) 키와 서비스 상태를 확인해주세요.", "action": "none", "target": None, "provider_error": True, "provider": "gemini"}
            except Exception as error:
                if is_certificate_verification_error(error):
                    return tls_certificate_failure("gemini")
                logger.exception("Gemini request failed")
                return {"response": "앗! Gemini 연결에 실패했어 ㅠㅠ 로그를 확인해줘.", "action": "none", "target": None, "provider_error": True, "provider": "gemini"}
