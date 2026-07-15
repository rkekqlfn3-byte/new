import json
import logging
import urllib.error
import urllib.request

from engine.llm.base import ProviderBase

logger = logging.getLogger(__name__)


class OllamaProvider(ProviderBase):
    def call(
        self,
        model_name,
        prompt,
        user_input,
        image_data=None,
        mode="command",
        temperature=0.2,
        stream_callback=None,
        *,
        opener,
    ):
        url = "http://localhost:11434/api/chat"
        headers = {
            "Content-Type": "application/json"
        }

        data = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": prompt}
            ],
            "stream": True if (mode in ["conversation", "question"] and stream_callback) else False,
            "options": {
                "temperature": temperature,
                "repeat_penalty": 1.2
            }
        }

        if mode in ["command", "json"]:
            data["format"] = "json"

        if isinstance(user_input, list):
            data["messages"].extend(user_input)
        else:
            data["messages"].append({"role": "user", "content": user_input})

        if image_data:
            try:
                base64_str = image_data.split(',')[1] if ',' in image_data else image_data
                data["messages"][-1]["images"] = [base64_str]
            except (AttributeError, IndexError, TypeError):
                logger.debug("Skipping malformed Ollama image data", exc_info=True)

        req = urllib.request.Request(url, json.dumps(data).encode("utf-8"), headers)
        try:
            with opener(req, timeout=60) as response:
                if data.get("stream"):
                    full_content = ""
                    for line in response:
                        if line:
                            try:
                                chunk = json.loads(line.decode("utf-8"))
                                text_chunk = chunk.get("message", {}).get("content", "")
                                if text_chunk:
                                    full_content += text_chunk
                                    stream_callback(text_chunk)
                            except (
                                UnicodeDecodeError,
                                json.JSONDecodeError,
                                AttributeError,
                                TypeError,
                            ):
                                logger.debug(
                                    "Skipping malformed Ollama stream chunk",
                                    exc_info=True,
                                )
                    return {"response": full_content, "action": "none", "target": None}
                else:
                    result = json.loads(response.read().decode("utf-8"))
                    content = result.get("message", {}).get("content", "")

                    if mode == "command":
                        return self.decode_command_content(content)
                    else:
                        return {"response": content, "action": "none", "target": None}
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            if e.code == 404 and "not found" in err_msg:
                return {"response": f"앗! Ollama에 '{model_name}' 모델이 설치되지 않은 것 같아. 터미널에서 'ollama run {model_name}' 명령어를 치거나, 설정 창에서 설치된 모델 이름으로 바꿔줘! 😭", "action": "none", "target": None, "provider_error": True, "provider": "ollama"}

            detailed_err = err_msg
            try:
                parsed_err = json.loads(err_msg)
                if "error" in parsed_err:
                    detailed_err = parsed_err["error"]
            except (json.JSONDecodeError, TypeError):
                logger.debug("Ollama error response was not JSON", exc_info=True)
            return {"response": f"앗! Ollama 서버 내부 오류가 발생했어 ㅠㅠ (상태: {e.code})<br><span style='color:#ff5555; font-size:0.85em;'>원인: {detailed_err}</span><br>GPU 드라이버(CUDA)가 구버전이거나 Ollama 호환성 문제일 수 있어!", "action": "none", "target": None, "provider_error": True, "provider": "ollama"}
        except Exception:
            logger.exception("Ollama request failed")
            return {"response": "앗! Ollama 서버와 연결할 수 없어 ㅠㅠ 서버가 켜져 있는지 확인해줘!", "action": "none", "target": None, "provider_error": True, "provider": "ollama"}
