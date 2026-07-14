import json
import os
import urllib.request
import urllib.error
import time
import logging
from engine.command_context import CommandContextBuilder
from engine.network import (
    is_certificate_verification_error,
    tls_certificate_failure,
    urlopen_verified,
)
from engine.runtime_paths import user_data_path
from engine.storage.json_store import safe_read_json

USER_MEMORY_PATH = user_data_path("user_memory.json")
logger = logging.getLogger(__name__)

COMMAND_PROMPT_TEMPLATE = """{system_prompt}

[앱 및 웹사이트 사전]
{dictionary_context}

[학습된 매크로(Learned Macros)]
{learned_macros_context}

위 사전을 참고하여 사용자의 명령을 분석하세요. 사용자의 명령이 여러 단계로 이루어져 있다면, 이를 순서대로 쪼개어 다단계 액션(actions 배열)으로 구성하세요.

[공통 행동 계획]
- 다음 동작만으로 처리할 수 있으면 Python 코드를 만들지 말고 반드시 `action_plan`을 사용하세요: open_app, focus_window, move_window, window_state, hotkey, type_text, wait, navigate_url, clipboard_set, clipboard_get, copy_file, move_file, create_folder, write_text_file, uia_click, uia_set_text, uia_select_file.
- action_plan의 plan 배열은 실행 순서이며 최대 12단계입니다.
- 각 plan 단계에는 action, target, direction, selector, keys, text, seconds, x, y, width, height를 모두 넣고 해당 없는 값은 빈 문자열·빈 배열·0을 사용하세요. selector는 automation_id, name, control_type, parent_name, ancestor_name, match_mode를 모두 가지며 해당 없는 문자열은 비우고 match_mode는 auto로 쓰세요. 파일 작업에는 overwrite도 넣고 기본값은 false로 하세요.
- 앱과 변경값은 `{{app}}`, `{{direction}}`, `{{text}}`처럼 learning.slots와 같은 슬롯 이름으로 참조하세요.
- `action_plan`은 학습 대상이므로 macro_name, description, learning.intent, learning.verbs, learning.utterances를 절대 비워 두지 마세요. 변경 가능한 앱이나 값이 있으면 learning.slots도 반드시 만들고 plan에서 리터럴 대신 그 슬롯을 사용하세요.
- action_plan의 app_name은 특정 앱 전용이면 해당 사전 이름, 여러 앱에 공통이면 `시스템`으로 작성하세요.
- move_window의 direction은 왼쪽/오른쪽/위/아래/가운데 중 하나이며, 직접 좌표를 쓸 때만 x/y/width/height를 사용하세요.
- 왼쪽/오른쪽/위/아래 절반 이동은 실행기가 현재 화면 해상도를 자동 계산합니다. 해상도를 모른다는 이유로 거절하지 말고 반드시 move_window 행동 계획을 만드세요.
- window_state의 direction은 최대화/최소화/복원 중 하나입니다.
- copy_file/move_file은 target에 원본 파일, text에 대상 경로를 넣으세요. create_folder/write_text_file은 target에 실제 절대 경로를 넣고 write_text_file의 text에는 저장할 내용을 넣으세요.
- copy_file/move_file/write_text_file에서 overwrite는 사용자가 "덮어써", "교체해"처럼 기존 파일 교체를 명확히 요청한 경우에만 true입니다. 기존 파일 존재 여부를 모르거나 사용자가 말하지 않았다면 반드시 false입니다.
- uia_click/uia_set_text는 target에 사전 앱 이름을 넣으세요. 화면에서 확인할 수 있는 정보만 selector에 넣되 가능하면 automation_id와 control_type을 우선하고, 그다음 name·parent_name·ancestor_name을 사용하세요. match_mode는 기본 auto이며 exact/normalized/contains 중 명확한 방식만 지정하세요. 추측한 식별자를 만들지 마세요. direction은 구형 호환용 컨트롤 이름이므로 구조화 selector를 만들 수 없을 때만 사용하세요. uia_set_text의 text에는 입력할 값을 넣으세요. uia_select_file은 target에 실제 파일 경로를 넣으세요.
- 앱을 연 뒤 hotkey 또는 type_text를 실행할 때는 입력 전에 반드시 같은 앱을 target으로 하는 focus_window 단계를 넣고, hotkey/type_text의 target에도 같은 앱 이름을 넣으세요.

[정확성 규칙]
- `open_app`의 target은 반드시 위 앱 및 웹사이트 사전에 실제로 존재하는 이름을 한 글자도 바꾸지 말고 사용하세요. 사전에 없는 앱 이름을 추측하거나 만들지 마세요.
- 파일을 읽는 요청과 앱을 실행하는 요청을 구분하세요. 문서 내용 분석은 `read_and_analyze`, 프로그램 실행만 `open_app`입니다.
- `read_and_analyze`의 target에는 사용자가 지정한 실제 파일 경로만 넣으세요. 경로를 알 수 없으면 임의로 만들지 말고 `none`을 반환하세요.
- 필요한 값이 불확실하거나 실행할 수 없는 요청이면 코드를 억지로 만들지 말고 `none`을 반환하고 response에서 이유를 설명하세요.
- `dynamic_code`와 `adapted_macro`의 code는 실행 가능한 완전한 파이썬 코드여야 합니다.
- 새 `dynamic_code`는 `import json, sys` 후 `args = json.loads(sys.argv[1])`로 인자를 읽어야 합니다. 코드에서 바뀔 수 있는 앱, 방향, 색상, 숫자 등을 하드코딩하지 말고 `args["슬롯명"]`을 사용하세요.
- 현재 실행 중인 특정 앱의 상태 조회처럼 코드에 주입할 사용자 변경값이 전혀 없는 작업은 가짜 슬롯을 만들지 말고 `learning.slots`를 빈 배열로 두세요.
- 동적 코드에서 `eval`, `exec`, `compile`, `__import__`, `os.system`, 셸 모드 subprocess, PowerShell·CMD, 레지스트리, 인증 정보 접근, 내려받은 코드 실행을 사용하지 마세요.
- `pygetwindow`, `tkinter`, `pyautogui`, `numpy`, `PIL`, `cv2`, `textual`은 현재 실행 파일에 없으므로 절대 import하지 마세요. Office 앱 제어는 `win32com.client`, 일반 창 정보는 `win32gui`, Windows 메시지 박스는 `win32api.MessageBox`, UI Automation은 `pywinauto`를 사용하세요.
- `win32api.MessageBox`는 JARVIS 창 뒤에 숨지 않도록 마지막 flags 인수에 `0x00050040`(`MB_TOPMOST | MB_SETFOREGROUND | MB_ICONINFORMATION`)을 사용하세요.
- JARVIS에서 명령을 입력하는 동안 JARVIS가 전면 창이 되므로, Excel처럼 사용자가 이름을 지정한 앱의 창 제목을 `win32gui.GetForegroundWindow()`로 읽지 마세요. Excel의 현재 창 제목은 반드시 `excel = win32com.client.GetActiveObject("Excel.Application")`, `window = excel.ActiveWindow`, `title = window.Caption` 순서로 읽고 `win32api.MessageBox`로 표시하세요. 이 조회를 위해 새 Excel을 `Dispatch`로 실행하지 마세요.
- 파일 변경·외부 프로세스·네트워크·COM·UI Automation은 실행 전 안전 확인 대상입니다. 공통 `action_plan`이나 네이티브 액션으로 표현할 수 있으면 Python보다 반드시 그것을 우선하세요.
- target은 실행 대상을 설명하는 200자 이내의 짧은 문자열이어야 합니다. 식별자나 반복 문자열을 길게 만들지 마세요.
- 모든 action 객체에는 action, target, app_name, macro_name, description, code, explanation_steps, learning 필드를 빠짐없이 넣으세요. 해당 없는 필드는 빈 문자열·빈 배열을 사용하세요.
- 새로 학습할 `dynamic_code`에는 반드시 `learning`을 작성하세요.
- `learning.intent`는 영문 대문자와 밑줄 형식의 행동 의도입니다. 예: MOVE_WINDOW, SET_CELL_COLOR.
- `learning.argument_mode`는 반드시 `json`입니다.
- `learning.verbs`는 사용자가 사용한 핵심 동사와 자연스러운 유사 동사입니다.
- `learning.nouns`는 명사별 text, canonical, type을 가집니다. 앱/사이트의 canonical은 위 사전에 실제 존재하는 정확한 이름만 사용하고, 확인할 수 없는 일반 명사는 canonical을 빈 문자열로 둡니다.
- `learning.utterances`에는 원문과 자연스러운 유사 표현을 넣고, 변경 가능한 부분은 `{{app}}`, `{{cell}}`, `{{color}}`처럼 표시한 템플릿도 추가하세요.
- `learning.slots`에는 변경 가능한 인자의 name, type, 현재 value, required를 기록하세요.

각 액션은 다음 상황 중 하나를 선택하세요.
1. (파일 읽기/요약): 사용자가 파일 내용을 읽고 요약/분석해 달라고 하면 'read_and_analyze' 액션과 절대 경로를 target으로 반환.
2. (학습된 매크로 완벽 일치): 기존 학습된 매크로와 완벽히 일치하면 'use_learned_macro' 액션과 해당 app_name, macro_name 반환.
3. (학습된 매크로 응용 - 명사 치환): [매우 중요] 기존 코드에서 파라미터(색상, 위치, 대상)만 바꾸면 된다면 'adapted_macro' 반환. (app_name, macro_name 유지, code에 변수가 치환된 파이썬 코드 포함).
4. (공통 Windows 행동): 공통 행동 계획으로 표현 가능하면 'action_plan'과 plan 배열을 반환.
5. (새로운 파이썬 제어): 공통 행동으로 표현할 수 없는 경우에만 파이썬을 활용한 윈도우 제어 코드를 작성하여 'dynamic_code' 액션 반환.
   **중요**: 하드코딩 절대 금지. `import json, sys` 후 `args = json.loads(sys.argv[1])`을 사용하여 모든 슬롯 값을 외부에서 주입받도록 작성.
   **중요**: 'dynamic_code'나 'adapted_macro' 반환 시 `explanation_steps` 배열을 반드시 함께 반환.
6. (단순 앱 실행): 사전에 등록된 단순 앱 실행은 'open_app'. target은 반드시 사전의 정확한 이름.

CRITICAL INSTRUCTION:
절대 마크다운(```json)을 사용하지 말고 아래 JSON 형식 단 하나만 출력하세요.
{{"response": "친절한 전체 답변 한 줄", "actions": [{{"action": "open_app" | "action_plan" | "dynamic_code" | "use_learned_macro" | "adapted_macro" | "read_and_analyze" | "none", "target": "앱/파일경로", "app_name": "앱 이름", "macro_name": "매크로 이름", "description": "설명", "code": "파이썬 코드", "explanation_steps": [{{"step": "설명", "code_snippet": "코드"}}], "plan": [{{"action": "move_window", "target": "{{app}}", "direction": "{{direction}}", "keys": [], "text": "", "seconds": 0, "x": 0, "y": 0, "width": 0, "height": 0}}], "learning": {{"intent": "MOVE_WINDOW", "argument_mode": "json", "verbs": ["옮겨", "이동해"], "nouns": [{{"text": "계산기", "canonical": "계산기", "type": "app"}}], "utterances": ["계산기를 오른쪽으로 옮겨", "{{app}}을 {{direction}}으로 옮겨"], "slots": [{{"name": "app", "type": "app", "value": "계산기", "required": true}}, {{"name": "direction", "type": "direction", "value": "오른쪽", "required": true}}]}}}}]}}"""

CONVERSATION_PROMPT_TEMPLATE = """{system_prompt}

당신은 친절하고 똑똑한 데스크탑 인공지능 비서 '자비스'입니다.
사용자의 질문에 친절하게 답변하되, 감정 연기나 상황극(역할극)은 절대 하지 마세요.
간결하고 명확하게 한국어로 답변하세요.
"""

QUESTION_PROMPT_TEMPLATE = """{system_prompt}

당신은 고도로 지능적이고 전문적인 지식 도우미입니다. 
사용자의 질문에 대해 객관적인 사실과 정보 전달에 집중하여 한국어로 답변하세요.
"""

class LLMEngine:
    def __init__(self, dict_mgr):
        self.dict_mgr = dict_mgr
        self.timeout = 60
        self.command_context_builder = CommandContextBuilder(dict_mgr)
        self.last_command_context_stats = {}

    @staticmethod
    def _decode_command_content(content):
        """Decode normal JSON and one common double-encoded provider response."""
        current = content
        for _ in range(2):
            if isinstance(current, str):
                try:
                    current = json.loads(current)
                except json.JSONDecodeError:
                    return {"response": current, "action": "none", "target": None}
            if not isinstance(current, dict):
                return {"response": str(current), "action": "none", "target": None}
            if (
                not isinstance(current.get("actions"), list)
                and isinstance(current.get("response"), str)
                and current["response"].lstrip().startswith("{")
            ):
                current = current["response"]
                continue
            return current
        return current if isinstance(current, dict) else {
            "response": str(current), "action": "none", "target": None
        }

    def _load_user_memory_context(self):
        if not os.path.exists(USER_MEMORY_PATH):
            return ""

        data = safe_read_json(USER_MEMORY_PATH, {})

        if not isinstance(data, dict):
            return ""

        sections = []
        user_info = data.get("user_info", data.get("userInfo", ""))
        rules = data.get("rules", "")
        others = data.get("others", "")

        if user_info:
            sections.append(f"[사용자 정보]\n{user_info}")
        if rules:
            sections.append(f"[응답 규칙]\n{rules}")
        if others:
            sections.append(f"[기타 기억]\n{others}")
        return "\n\n".join(sections)

    def _get_command_dictionary_context(self, user_input=""):
        """Compatibility helper returning the filtered app context."""
        return self.command_context_builder.build(user_input).dictionary_context

    @staticmethod
    def _attach_command_candidates(result, command_context):
        if not isinstance(result, dict) or command_context is None:
            return result
        result["_allowed_app_candidates"] = list(command_context.allowed_apps)
        result["_allowed_learned_candidates"] = [
            [app_name, macro_name]
            for app_name, macro_name in command_context.allowed_macros
        ]
        return result

    def _call_openai(self, api_key, prompt, user_input, image_data=None, mode="command", temperature=0.2, stream_callback=None):
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
            with urlopen_verified(req, timeout=self.timeout) as response:
                if data.get("stream"):
                    full_content = ""
                    for line in response:
                        decoded_line = line.decode('utf-8').strip()
                        if decoded_line.startswith("data: "):
                            data_str = decoded_line[6:]
                            if data_str == "[DONE]": break
                            try:
                                chunk = json.loads(data_str)
                                delta = chunk["choices"][0].get("delta", {})
                                text_chunk = delta.get("content", "")
                                if text_chunk:
                                    full_content += text_chunk
                                    stream_callback(text_chunk)
                            except: pass
                    return {"response": full_content, "action": "none", "target": None}
                else:
                    result = json.loads(response.read().decode("utf-8"))
                    content = result["choices"][0]["message"]["content"]
                    
                    if mode == "command":
                        return self._decode_command_content(content)
                    else:
                        return {"response": content, "action": "none", "target": None}
        except urllib.error.HTTPError as e:
            return {"response": f"앗! OpenAI API 에러! ({e.code})", "action": "none", "target": None, "provider_error": True, "provider": "openai"}
        except Exception as e:
            if is_certificate_verification_error(e):
                return tls_certificate_failure("openai")
            return {"response": f"앗! OpenAI 연결에 실패했어 ㅠㅠ (네트워크 에러: {str(e)})", "action": "none", "target": None, "provider_error": True, "provider": "openai"}

    def _call_gemini(self, api_key, prompt, user_input, image_data=None, mode="command", temperature=0.2, stream_callback=None):
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
                    except Exception:
                        pass
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
                except Exception:
                    pass
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
                with urlopen_verified(req, timeout=self.timeout) as response:
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
                                except: pass
                        return {"response": full_content, "action": "none", "target": None}
                    else:
                        result = json.loads(response.read().decode("utf-8"))
                        content = result["candidates"][0]["content"]["parts"][0]["text"]
                        content = content.strip()
                        
                        if mode == "command":
                            return self._decode_command_content(content)
                        else:
                            return {"response": content, "action": "none", "target": None}
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                if e.code in [503, 429] and attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return {"response": f"Gemini API 에러! (상태: {e.code}) 키가 올바른지 확인해주세요. 세부내용: {err_body[:100]}", "action": "none", "target": None, "provider_error": True, "provider": "gemini"}
            except Exception as e:
                if is_certificate_verification_error(e):
                    return tls_certificate_failure("gemini")
                return {"response": f"앗! Gemini 연결에 실패했어 ㅠㅠ (네트워크 에러: {str(e)})", "action": "none", "target": None, "provider_error": True, "provider": "gemini"}

    def _call_ollama(self, model_name, prompt, user_input, image_data=None, mode="command", temperature=0.2, stream_callback=None):
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
            except:
                pass
        
        req = urllib.request.Request(url, json.dumps(data).encode("utf-8"), headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
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
                            except: pass
                    return {"response": full_content, "action": "none", "target": None}
                else:
                    result = json.loads(response.read().decode("utf-8"))
                    content = result.get("message", {}).get("content", "")
                    
                    if mode == "command":
                        return self._decode_command_content(content)
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
            except Exception:
                pass
            return {"response": f"앗! Ollama 서버 내부 오류가 발생했어 ㅠㅠ (상태: {e.code})<br><span style='color:#ff5555; font-size:0.85em;'>원인: {detailed_err}</span><br>GPU 드라이버(CUDA)가 구버전이거나 Ollama 호환성 문제일 수 있어!", "action": "none", "target": None, "provider_error": True, "provider": "ollama"}
        except Exception as e:
            return {"response": f"앗! Ollama 서버와 연결할 수 없어 ㅠㅠ 서버가 켜져 있는지 확인해줘! ({str(e)})", "action": "none", "target": None, "provider_error": True, "provider": "ollama"}

    def _invoke_provider(
        self, provider, api_key, ollama_model, prompt, user_input, image_data,
        mode, stream_callback,
    ):
        if provider == "ollama":
            return self._call_ollama(
                ollama_model, prompt, user_input, image_data, mode,
                stream_callback=stream_callback,
            )
        if provider == "openai":
            return self._call_openai(
                api_key, prompt, user_input, image_data, mode,
                stream_callback=stream_callback,
            )
        if provider == "gemini":
            return self._call_gemini(
                api_key, prompt, user_input, image_data, mode,
                stream_callback=stream_callback,
            )
        return {
            "response": "알 수 없는 AI 제공자입니다. 설정을 확인해주세요.",
            "action": "none", "target": None, "provider_error": True,
            "provider": provider,
        }

    def process_command(self, user_input, image_data=None, mode="command", use_api=False, summary="", stream_callback=None, conversation_state=None):
        config = self.dict_mgr.get_ai_config()
        provider = config.get("provider", "openai")
        api_key = config.get("api_key", "").strip()
        ollama_model = config.get("ollama_model", "llama3")
        routing_mode = config.get("routing_mode", "auto")
        
        system_prompt = "당신은 사용자의 컴퓨터 제어를 돕는 유능한 데스크탑 AI 어시스턴트입니다."
        
        if summary:
            system_prompt += f"\n\n[이전 대화 요약 (Context)]\n다음 정보는 최근까지의 핵심 대화 요약입니다. 이를 바탕으로 기억과 문맥을 자연스럽게 이어가세요:\n{summary}"
        
        memory_content = self._load_user_memory_context()
        if memory_content:
            system_prompt += (
                "\n\n[사용자 영구 기억 (Personal Intelligence)]\n"
                "다음 정보는 사용자가 직접 저장한 기억입니다. 질문과 명령을 처리할 때 이 정보를 우선 참고하세요:\n"
                f"{memory_content}"
            )
                
        if mode == "command" and routing_mode in {"auto", "ai_first"}:
            actual_provider = provider if api_key else "ollama"
        else:
            actual_provider = provider if use_api else "ollama"
        
        if actual_provider != "ollama" and not api_key:
            return {"response": "API 키가 없습니다. [⚙️ AI 설정]에서 먼저 입력해주세요.", "action": "none", "target": None, "no_api_key": True}
        
        command_context = None
        if mode == "conversation":
            prompt = CONVERSATION_PROMPT_TEMPLATE.format(system_prompt=system_prompt)
        elif mode == "question":
            prompt = QUESTION_PROMPT_TEMPLATE.format(system_prompt=system_prompt)
        else:
            context_started = time.perf_counter()
            command_context = self.command_context_builder.build(user_input)
            prompt = COMMAND_PROMPT_TEMPLATE.format(
                system_prompt=system_prompt,
                dictionary_context=command_context.dictionary_context,
                learned_macros_context=command_context.learned_macros_context,
            )
            self.last_command_context_stats = {
                "app_candidates": command_context.app_candidates,
                "learned_candidates": command_context.learned_candidates,
                "prompt_chars": len(prompt),
                "selection_ms": round((time.perf_counter() - context_started) * 1000, 2),
                "provider": actual_provider,
            }
            logger.info(
                "AI command context: apps=%d macros=%d chars=%d selection_ms=%.2f",
                command_context.app_candidates,
                command_context.learned_candidates,
                len(prompt),
                self.last_command_context_stats["selection_ms"],
            )

        provider_started = time.perf_counter()
        result = self._invoke_provider(
            actual_provider, api_key, ollama_model, prompt, user_input,
            image_data, mode, stream_callback,
        )
        if (
            mode == "command"
            and routing_mode in {"auto", "ai_first"}
            and actual_provider != "ollama"
            and result.get("provider_error")
            and not result.get("tls_certificate_error")
        ):
            fallback = self._invoke_provider(
                "ollama", api_key, ollama_model, prompt, user_input,
                image_data, mode, stream_callback,
            )
            if not fallback.get("provider_error"):
                fallback["routing"] = "ollama_fallback"
                fallback["fallback_from"] = actual_provider
                self.last_command_context_stats["provider_ms"] = round(
                    (time.perf_counter() - provider_started) * 1000, 2
                )
                return self._attach_command_candidates(fallback, command_context)
            result["response"] = (
                f"{result.get('response', '클라우드 AI 호출 실패')}\n\n"
                f"Ollama 대체도 실패했습니다: {fallback.get('response', '')}"
            )
            result["fallback_error"] = True
            self.last_command_context_stats["provider_ms"] = round(
                (time.perf_counter() - provider_started) * 1000, 2
            )
            return self._attach_command_candidates(result, command_context)

        result.setdefault(
            "routing",
            "ollama_no_api_key"
            if mode == "command" and actual_provider == "ollama" and not api_key
            else actual_provider,
        )
        if mode == "command":
            self.last_command_context_stats["provider_ms"] = round(
                (time.perf_counter() - provider_started) * 1000, 2
            )
        return self._attach_command_candidates(result, command_context)
