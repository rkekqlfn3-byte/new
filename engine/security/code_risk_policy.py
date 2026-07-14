"""Conservative policy constants for dynamic Python actions."""

POLICY_VERSION = 2
MAX_CODE_CHARACTERS = 50_000
MAX_AST_NODES = 5_000
MAX_STRING_LITERAL_CHARACTERS = 10_000


SAFE_MODULE_PREFIXES = frozenset({
    "array",
    "bisect",
    "calendar",
    "collections",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "hashlib",
    "heapq",
    "html",
    "io",
    "itertools",
    "json",
    "math",
    "numbers",
    "operator",
    "os",
    "pathlib",
    "platform",
    "pprint",
    "random",
    "re",
    "shutil",
    "statistics",
    "string",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "textwrap",
    "typing",
    "unicodedata",
    "urllib.parse",
    "uuid",
    "win32api",
    "win32con",
    "zoneinfo",
})

CONFIRM_MODULE_PREFIXES = {
    "aiohttp": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "comtypes": ("app_automation", "COM을 통해 다른 앱을 조작할 수 있습니다."),
    "ftplib": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "glob": ("broad_file_access", "여러 파일을 탐색할 수 있습니다."),
    "http.client": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "keyboard": ("system_control", "키보드 입력을 자동으로 보낼 수 있습니다."),
    "mouse": ("system_control", "마우스 입력을 자동으로 보낼 수 있습니다."),
    "openpyxl": ("file_write", "Excel 파일을 직접 변경할 수 있습니다."),
    "pandas": ("file_write", "데이터 파일을 읽거나 변경할 수 있습니다."),
    "psutil": ("system_control", "실행 중인 프로세스를 조사하거나 제어할 수 있습니다."),
    "pywinauto": ("app_automation", "UI Automation으로 다른 앱을 조작할 수 있습니다."),
    "pythoncom": ("app_automation", "COM을 통해 다른 앱을 조작할 수 있습니다."),
    "requests": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "selenium": ("network", "브라우저를 통해 외부 사이트와 통신할 수 있습니다."),
    "smtplib": ("network", "외부로 데이터를 전송할 수 있습니다."),
    "socket": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "sqlite3": ("file_write", "로컬 데이터베이스를 변경할 수 있습니다."),
    "tarfile": ("broad_file_access", "여러 파일을 압축하거나 풀 수 있습니다."),
    "uiautomation": ("app_automation", "UI Automation으로 다른 앱을 조작할 수 있습니다."),
    "urllib.request": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "webbrowser": ("process_execution", "브라우저나 외부 URL을 열 수 있습니다."),
    "websocket": ("network", "외부 네트워크 통신 모듈을 사용합니다."),
    "win32com": ("app_automation", "COM을 통해 다른 앱을 조작할 수 있습니다."),
    "win32clipboard": ("system_control", "Windows 클립보드를 읽거나 변경할 수 있습니다."),
    "win32gui": ("system_control", "다른 프로그램의 창을 조작할 수 있습니다."),
    "win32process": ("system_control", "실행 중인 프로세스를 조사하거나 제어할 수 있습니다."),
    "zipfile": ("broad_file_access", "여러 파일을 압축하거나 풀 수 있습니다."),
}

# These dependencies are intentionally absent from the lightweight packaged
# executable.  Keeping them separate from unknown imports lets the user and the
# code generator see a useful, supported replacement instead of a vague error.
UNSUPPORTED_MODULE_PREFIXES = {
    "cv2": (
        "unsupported_dependency",
        "현재 JARVIS 실행 파일에 포함되지 않은 모듈입니다: cv2",
    ),
    "numpy": (
        "unsupported_dependency",
        "현재 JARVIS 실행 파일에 포함되지 않은 모듈입니다: numpy",
    ),
    "PIL": (
        "unsupported_dependency",
        "현재 JARVIS 실행 파일에 포함되지 않은 모듈입니다: PIL",
    ),
    "pyautogui": (
        "unsupported_dependency",
        "현재 JARVIS 실행 파일에 포함되지 않은 모듈입니다: pyautogui. UI 제어는 pywinauto 또는 win32 API를 사용해야 합니다.",
    ),
    "pygetwindow": (
        "unsupported_dependency",
        "현재 JARVIS 실행 환경에 없는 모듈입니다: pygetwindow. 창 정보는 win32gui를 사용해야 합니다.",
    ),
    "textual": (
        "unsupported_dependency",
        "현재 JARVIS 실행 파일에 포함되지 않은 모듈입니다: textual",
    ),
    "tkinter": (
        "unsupported_dependency",
        "현재 JARVIS 실행 파일에 포함되지 않은 모듈입니다: tkinter. 메시지 박스는 win32api.MessageBox를 사용해야 합니다.",
    ),
}

BLOCK_MODULE_PREFIXES = {
    "browser_cookie3": ("credential_access", "브라우저 인증 정보에 접근할 수 있습니다."),
    "importlib": ("dynamic_execution", "실행 중 모듈을 동적으로 불러올 수 있습니다."),
    "keyring": ("credential_access", "운영체제의 보안 정보 저장소에 접근할 수 있습니다."),
    "marshal": ("dynamic_execution", "검사하기 어려운 바이트 코드를 불러올 수 있습니다."),
    "pickle": ("dynamic_execution", "역직렬화 과정에서 임의 코드가 실행될 수 있습니다."),
    "win32crypt": ("credential_access", "Windows 보호 정보를 복호화할 수 있습니다."),
    "win32net": ("system_control", "Windows 계정 또는 네트워크 설정에 접근할 수 있습니다."),
    "win32security": ("credential_access", "Windows 보안 토큰과 권한에 접근할 수 있습니다."),
    "win32service": ("system_control", "Windows 서비스 설정을 변경할 수 있습니다."),
    "win32serviceutil": ("system_control", "Windows 서비스를 제어하거나 변경할 수 있습니다."),
    "winreg": ("registry", "Windows 레지스트리에 접근하거나 변경할 수 있습니다."),
}

BLOCKED_BUILTINS = frozenset({
    "__import__",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "locals",
    "setattr",
    "vars",
})

BLOCKED_INTROSPECTION_ATTRIBUTES = frozenset({
    "__bases__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__dict__",
    "__func__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__setattr__",
    "__subclasses__",
})

SUBPROCESS_CALLS = frozenset({
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
})

BLOCKED_SHELL_EXECUTABLES = frozenset({
    "cmd",
    "cmd.exe",
    "cscript",
    "cscript.exe",
    "mshta",
    "mshta.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "reg",
    "reg.exe",
    "rundll32",
    "rundll32.exe",
    "schtasks",
    "schtasks.exe",
    "wscript",
    "wscript.exe",
})

BLOCKED_SCRIPT_SUFFIXES = (".bat", ".cmd", ".ps1", ".vbs", ".wsf")

SENSITIVE_NAME_PARTS = frozenset({
    ".ssh",
    "api_key",
    "apikey",
    "auth_token",
    "cookie",
    "credential",
    "dictionaries.json",
    "execution_diagnostics.json",
    "id_rsa",
    "id_ed25519",
    "key4.db",
    "login data",
    "logins.json",
    "password",
    "private_key",
    "secret",
    "session_token",
    "user_preferences.json",
    "web data",
})

PROTECTED_PATH_PARTS = frozenset({
    "engine/parser.py",
    "engine/security/",
    "jarvis.spec",
    "jarvis_app.py",
})
