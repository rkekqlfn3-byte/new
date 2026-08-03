"""AST-based preflight for generated and learned Python actions.

This is intentionally a conservative risk classifier, not a sandbox. Code that
cannot be understood well enough is blocked instead of being guessed safe.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path

from engine.runtime_paths import PROJECT_ROOT
from engine.security.code_risk_policy import (
    BLOCK_MODULE_PREFIXES,
    BLOCKED_BUILTINS,
    BLOCKED_INTROSPECTION_ATTRIBUTES,
    BLOCKED_SCRIPT_SUFFIXES,
    BLOCKED_SHELL_EXECUTABLES,
    CONFIRM_MODULE_PREFIXES,
    MAX_AST_NODES,
    MAX_CODE_CHARACTERS,
    MAX_STRING_LITERAL_CHARACTERS,
    POLICY_VERSION,
    PROTECTED_PATH_PARTS,
    SAFE_MODULE_PREFIXES,
    SENSITIVE_NAME_PARTS,
    SUBPROCESS_CALLS,
    UNSUPPORTED_MODULE_PREFIXES,
)
from engine.security.risk_models import (
    BLOCKED,
    CONFIRMATION_REQUIRED,
    SAFE,
    DynamicCodePreflightResult,
    RiskFinding,
)

_INDIRECT_CAPABILITY_OBJECTS = frozenset({
    "aiohttp", "comtypes", "ctypes", "ftplib", "glob", "http.client",
    "keyboard", "mouse", "open", "openpyxl", "os", "pandas", "pathlib.Path",
    "psutil", "pywinauto", "pythoncom", "requests", "selenium", "shutil",
    "smtplib", "socket", "sqlite3", "subprocess", "tarfile", "tempfile",
    "uiautomation", "urllib.request", "webbrowser", "websocket", "win32api",
    "win32clipboard", "win32com", "win32gui", "win32process", "zipfile",
})


def _matches_prefix(value, prefixes) -> bool:
    return any(value == prefix or value.startswith(prefix + ".") for prefix in prefixes)


def _string_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_strings(node):
    values = []
    for item in ast.walk(node) if isinstance(node, ast.AST) else ():
        value = _string_value(item)
        if value is not None:
            values.append(value)
    return values


class _RiskVisitor(ast.NodeVisitor):
    def __init__(
        self, aliases, direct_call_targets=None, nested_reference_parts=None
    ):
        self.aliases = aliases
        self.direct_call_targets = set(direct_call_targets or ())
        self.nested_reference_parts = set(nested_reference_parts or ())
        self.findings = []
        self._seen = set()
        self.has_broad_file_access = False
        self.has_file_delete = False
        self.has_file_read = False
        self.has_file_write = False

    def add(self, code, category, disposition, message, node=None):
        line = getattr(node, "lineno", None)
        key = (code, category, disposition, line)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(RiskFinding(
            code=code,
            category=category,
            disposition=disposition,
            message=message,
            line=line,
        ))

    def qualified_name(self, node):
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.qualified_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _check_indirect_callable_reference(self, node, qualified):
        """Block risky capabilities when they are moved outside a direct call.

        Direct calls are inspected together with their arguments in ``visit_Call``.
        Once the callable is placed in a container, destructured, or passed to
        another function, the current static policy can no longer prove which
        arguments will reach it.  Keeping that capability is therefore a
        fail-closed condition rather than an implicit SAFE result.
        """
        if (
            not qualified
            or node in self.direct_call_targets
            or node in self.nested_reference_parts
        ):
            return
        terminal = qualified.rsplit(".", 1)[-1]
        terminal_folded = terminal.casefold()
        risky = (
            qualified in _INDIRECT_CAPABILITY_OBJECTS
            or qualified in BLOCKED_BUILTINS
            or terminal_folded in BLOCKED_BUILTINS
            or terminal_folded in {
                "deletefile", "deletefolder", "getenv", "loadlibrary",
                "load_library", "open", "regdelete", "regread", "regwrite",
            }
            or qualified in {
                "os.system", "os.popen", "subprocess.getoutput",
                "subprocess.getstatusoutput", "ctypes.CDLL", "ctypes.PyDLL",
                "ctypes.WinDLL", "ctypes.cdll.LoadLibrary",
                "ctypes.windll.LoadLibrary", "os.startfile", "os.kill",
                "os.killpg", "shutil.rmtree", "win32api.CreateProcess",
                "win32api.ShellExecute", "win32api.WinExec",
                "win32process.CreateProcess", "win32api.MessageBox",
            }
            or qualified in SUBPROCESS_CALLS
            or qualified.startswith((
                "os.exec", "os.spawn", "os.environ.", "winreg.",
                "win32api.Reg",
            ))
            or qualified in {
                "io.FileIO", "io.open", "pathlib.Path.open",
                "numpy.memmap", "numpy.save", "numpy.savetxt", "numpy.savez",
                "numpy.savez_compressed", "os.remove", "os.unlink",
                "os.rmdir", "shutil.move", "os.chmod", "os.chown",
                "os.lchmod", "os.link", "os.mkdir", "os.makedirs",
                "os.rename", "os.renames", "os.replace", "os.truncate",
                "shutil.copy", "shutil.copy2", "shutil.copyfile",
                "shutil.copytree", "shutil.chown", "shutil.make_archive",
                "shutil.unpack_archive", "tempfile.NamedTemporaryFile",
                "tempfile.TemporaryDirectory", "tempfile.mkstemp",
                "tempfile.mkdtemp", "os.listdir", "os.scandir", "os.walk",
                "glob.glob", "glob.iglob",
            }
            or terminal_folded in {
                "chmod", "deletefile", "deletefolder", "glob", "hardlink_to",
                "iterdir", "mkdir", "read_bytes", "read_text", "rename",
                "replace", "rglob", "rmdir", "symlink_to", "tofile", "touch",
                "unlink", "write_bytes", "write_text",
            }
            or self._is_network_call(qualified)
            or self._is_ui_control_call(qualified)
            or self._is_sensitive_api(qualified)
        )
        if risky:
            self.add(
                f"indirect_capability_reference:{qualified}",
                "dynamic_execution",
                BLOCKED,
                "위험 기능을 간접 호출 형태로 전달해 안전하게 검사할 수 없습니다.",
                node,
            )

    def visit_Import(self, node):
        for alias in node.names:
            self._classify_module(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level or any(alias.name == "*" for alias in node.names):
            self.add(
                "unresolved_import",
                "dynamic_execution",
                BLOCKED,
                "정적으로 확인할 수 없는 상대 경로 또는 와일드카드 import입니다.",
                node,
            )
        self._classify_module(node.module or "", node)
        self.generic_visit(node)

    def _classify_module(self, module, node):
        if not module or module == "__future__":
            return
        for prefix, (category, message) in BLOCK_MODULE_PREFIXES.items():
            if _matches_prefix(module, {prefix}):
                self.add(
                    f"blocked_module:{prefix}", category, BLOCKED, message, node
                )
                return
        for prefix, (category, message) in UNSUPPORTED_MODULE_PREFIXES.items():
            if _matches_prefix(module, {prefix}):
                self.add(
                    f"unsupported_module:{prefix}", category, BLOCKED, message, node
                )
                return
        for prefix, (category, message) in CONFIRM_MODULE_PREFIXES.items():
            if _matches_prefix(module, {prefix}):
                self.add(
                    f"confirm_module:{prefix}",
                    category,
                    CONFIRMATION_REQUIRED,
                    message,
                    node,
                )
                return
        if module == "ctypes" or module.startswith("ctypes."):
            return
        if not _matches_prefix(module, SAFE_MODULE_PREFIXES):
            self.add(
                f"unknown_import:{module}",
                "unknown_import",
                BLOCKED,
                f"안전 정책에 등록되지 않은 모듈입니다: {module}",
                node,
            )

    def visit_Attribute(self, node):
        if node.attr in BLOCKED_INTROSPECTION_ATTRIBUTES:
            self.add(
                f"blocked_attribute:{node.attr}",
                "dynamic_execution",
                BLOCKED,
                f"검사를 우회할 수 있는 내부 속성을 사용합니다: {node.attr}",
                node,
            )
        qualified = self.qualified_name(node)
        self._check_indirect_callable_reference(node, qualified)
        if qualified == "os.environ":
            self.add(
                "environment_access",
                "system_information",
                CONFIRMATION_REQUIRED,
                "프로세스 환경 변수에 접근합니다.",
                node,
            )
        if qualified.startswith("ctypes.windll."):
            allowed_user32 = {
                "ctypes.windll.user32.keybd_event",
                "ctypes.windll.user32.mouse_event",
                "ctypes.windll.user32.SetCursorPos",
            }
            if qualified in allowed_user32:
                self.add(
                    "ctypes_user_input",
                    "system_control",
                    CONFIRMATION_REQUIRED,
                    "Windows 입력이나 커서를 직접 조작합니다.",
                    node,
                )
            elif qualified.count(".") >= 3:
                self.add(
                    "ctypes_native_call",
                    "system_control",
                    BLOCKED,
                    "검사하기 어려운 Windows 네이티브 함수를 직접 호출합니다.",
                    node,
                )
        self.generic_visit(node)

    def visit_Assign(self, node):
        qualified = self.qualified_name(node.value)
        if qualified:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = qualified
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        qualified = self.qualified_name(node.value) if node.value else ""
        if qualified and isinstance(node.target, ast.Name):
            self.aliases[node.target.id] = qualified
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        qualified = self.qualified_name(node.value)
        if qualified and isinstance(node.target, ast.Name):
            self.aliases[node.target.id] = qualified
        self.generic_visit(node)

    def visit_Name(self, node):
        self._check_indirect_callable_reference(node, self.qualified_name(node))
        if node.id == "__builtins__":
            self.add(
                "blocked_builtins_access",
                "dynamic_execution",
                BLOCKED,
                "Python 내장 함수 테이블에 직접 접근하려고 합니다.",
                node,
            )
        self.generic_visit(node)

    def visit_Subscript(self, node):
        qualified = self.qualified_name(node.value)
        if qualified in {"os.environ", "environ"}:
            key = _string_value(node.slice)
            if key and self._is_sensitive_name(key):
                self.add(
                    "sensitive_environment_access",
                    "credential_access",
                    BLOCKED,
                    "환경 변수에서 보안 정보에 접근하려고 합니다.",
                    node,
                )
            else:
                self.add(
                    "environment_access",
                    "system_information",
                    CONFIRMATION_REQUIRED,
                    "프로세스 환경 변수에 접근합니다.",
                    node,
                )
        self.generic_visit(node)

    def visit_Call(self, node):
        qualified = self.qualified_name(node.func)
        terminal = qualified.rsplit(".", 1)[-1]
        terminal_folded = terminal.casefold()

        if qualified in BLOCKED_BUILTINS or terminal_folded in BLOCKED_BUILTINS:
            self.add(
                f"blocked_builtin:{terminal}",
                "dynamic_execution",
                BLOCKED,
                f"정적 검사를 우회할 수 있는 함수입니다: {terminal}",
                node,
            )
        elif terminal_folded in {"loadlibrary", "load_library"}:
            self.add(
                "native_library_load",
                "dynamic_execution",
                BLOCKED,
                "검사되지 않은 네이티브 라이브러리를 불러오려고 합니다.",
                node,
            )
        elif terminal_folded in {"regdelete", "regread", "regwrite"}:
            self.add(
                "registry_access",
                "registry",
                BLOCKED,
                "Windows 레지스트리에 접근하거나 변경하려고 합니다.",
                node,
            )
        elif terminal_folded == "deletefolder":
            self.has_file_delete = True
            self.has_broad_file_access = True
            self.add(
                "recursive_delete",
                "broad_file_mutation",
                BLOCKED,
                "폴더 전체를 삭제하려고 합니다.",
                node,
            )
        elif terminal_folded == "deletefile":
            self.has_file_delete = True
            self.add(
                "file_delete",
                "file_delete",
                CONFIRMATION_REQUIRED,
                "파일을 삭제하려고 합니다.",
                node,
            )
        elif qualified in {"os.system", "os.popen"}:
            self.add(
                f"blocked_process:{qualified}",
                "process_execution",
                BLOCKED,
                "셸 명령을 직접 실행하려고 합니다.",
                node,
            )
        elif qualified in {"subprocess.getoutput", "subprocess.getstatusoutput"}:
            self.add(
                "subprocess_implicit_shell",
                "process_execution",
                BLOCKED,
                "내부적으로 셸을 사용하는 subprocess 함수는 허용하지 않습니다.",
                node,
            )
        elif qualified.startswith(("os.exec", "os.spawn")):
            self.add(
                "low_level_process_execution",
                "process_execution",
                BLOCKED,
                "검사하기 어려운 저수준 프로세스 실행 함수는 허용하지 않습니다.",
                node,
            )
        elif qualified in SUBPROCESS_CALLS:
            self._check_subprocess(node, qualified)
        elif qualified.startswith("winreg."):
            self.add(
                "registry_access",
                "registry",
                BLOCKED,
                "Windows 레지스트리에 접근하거나 변경하려고 합니다.",
                node,
            )
        elif qualified.startswith("win32api.Reg"):
            self.add(
                "registry_access",
                "registry",
                BLOCKED,
                "Windows 레지스트리에 접근하거나 변경하려고 합니다.",
                node,
            )
        elif qualified in {
            "ctypes.CDLL", "ctypes.PyDLL", "ctypes.WinDLL",
            "ctypes.cdll.LoadLibrary", "ctypes.windll.LoadLibrary",
        }:
            self.add(
                "native_library_load",
                "dynamic_execution",
                BLOCKED,
                "검사되지 않은 네이티브 라이브러리를 불러오려고 합니다.",
                node,
            )
        elif qualified in {
            "open", "io.FileIO", "io.open", "pathlib.Path.open",
        } or terminal == "open":
            self._check_open(node)
        elif qualified == "os.getenv":
            self._check_getenv(node)
        elif qualified.startswith("os.environ."):
            self._check_environ_call(node)
        elif qualified == "numpy.load" and any(
            keyword.arg == "allow_pickle"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            self.add(
                "unsafe_numpy_deserialization",
                "dynamic_execution",
                BLOCKED,
                "pickle을 허용한 NumPy 역직렬화는 실행 코드가 포함될 수 있습니다.",
                node,
            )
        elif qualified in {
            "numpy.memmap", "numpy.save", "numpy.savetxt", "numpy.savez",
            "numpy.savez_compressed",
        } or terminal == "tofile":
            self.has_file_write = True
            self.add(
                "file_write",
                "file_write",
                CONFIRMATION_REQUIRED,
                "데이터를 파일로 생성·수정·덮어쓸 수 있습니다.",
                node,
            )
        elif self._is_network_call(qualified):
            self.add(
                "network_request",
                "network",
                CONFIRMATION_REQUIRED,
                "외부 네트워크로 요청하거나 데이터를 전송할 수 있습니다.",
                node,
            )
        elif qualified in {
            "os.startfile", "win32api.CreateProcess", "win32api.ShellExecute",
            "win32api.WinExec", "win32process.CreateProcess",
        }:
            self._check_external_start(node, qualified)
        elif qualified in {"os.kill", "os.killpg"}:
            self.add(
                "process_control",
                "system_control",
                CONFIRMATION_REQUIRED,
                "실행 중인 프로세스를 종료할 수 있습니다.",
                node,
            )
        elif qualified == "shutil.rmtree":
            self.has_file_delete = True
            self.has_broad_file_access = True
            self.add(
                "recursive_delete",
                "broad_file_mutation",
                BLOCKED,
                "폴더 전체를 재귀적으로 삭제하려고 합니다.",
                node,
            )
        elif qualified in {
            "os.remove", "os.unlink", "os.rmdir", "shutil.move",
        } or terminal in {"unlink", "rmdir"}:
            self.has_file_delete = True
            self.add(
                "file_delete",
                "file_delete",
                CONFIRMATION_REQUIRED,
                "파일이나 폴더를 삭제 또는 이동할 수 있습니다.",
                node,
            )
            self._block_protected_path(node)
        elif qualified in {
            "os.chmod", "os.chown", "os.lchmod", "os.link", "os.mkdir", "os.makedirs",
            "os.rename", "os.renames", "os.replace", "os.truncate", "shutil.copy",
            "shutil.copy2", "shutil.copyfile", "shutil.copytree",
            "shutil.chown", "shutil.make_archive", "shutil.unpack_archive",
            "tempfile.NamedTemporaryFile", "tempfile.TemporaryDirectory",
            "tempfile.mkstemp", "tempfile.mkdtemp",
        } or terminal in {
            "chmod", "hardlink_to", "mkdir", "rename", "replace", "symlink_to", "touch",
            "write_bytes", "write_text",
        }:
            self.has_file_write = True
            self.add(
                "file_write",
                "file_write",
                CONFIRMATION_REQUIRED,
                "파일이나 폴더를 생성·수정·덮어쓸 수 있습니다.",
                node,
            )
            self._block_protected_path(node)
        elif qualified in {
            "os.listdir", "os.scandir", "os.walk", "glob.glob", "glob.iglob",
        } or terminal in {"glob", "iterdir", "rglob"}:
            self.has_broad_file_access = True
            self.add(
                "broad_file_access",
                "broad_file_access",
                CONFIRMATION_REQUIRED,
                "여러 파일이나 하위 폴더를 광범위하게 탐색합니다.",
                node,
            )
        elif terminal in {"read_bytes", "read_text"}:
            self.has_file_read = True
            if self._contains_sensitive_name(_literal_strings(node)):
                self.add(
                    "sensitive_file_access",
                    "credential_access",
                    BLOCKED,
                    "인증 정보나 JARVIS 개인 데이터 파일에 접근합니다.",
                    node,
                )
        elif qualified == "win32api.MessageBox":
            self.add(
                "windows_message_box",
                "system_control",
                CONFIRMATION_REQUIRED,
                "Windows 메시지 박스를 화면에 표시합니다.",
                node,
            )
        elif self._is_ui_control_call(qualified):
            self.add(
                "system_or_app_control",
                "system_control",
                CONFIRMATION_REQUIRED,
                "다른 앱의 화면이나 입력을 자동으로 조작합니다.",
                node,
            )
        elif self._is_sensitive_api(qualified):
            self.add(
                "credential_api",
                "credential_access",
                BLOCKED,
                "보안 정보나 인증 정보에 접근할 수 있는 API입니다.",
                node,
            )
        elif qualified.startswith("win32api."):
            self.add(
                "win32_system_api",
                "system_control",
                CONFIRMATION_REQUIRED,
                "Windows 시스템 API를 직접 호출합니다.",
                node,
            )

        self.generic_visit(node)

    def _check_subprocess(self, node, qualified):
        shell_value = None
        for keyword in node.keywords:
            if keyword.arg == "shell":
                shell_value = keyword.value.value if isinstance(keyword.value, ast.Constant) else None
                if shell_value is not False:
                    self.add(
                        "subprocess_shell",
                        "process_execution",
                        BLOCKED,
                        "subprocess의 셸 실행은 허용하지 않습니다.",
                        node,
                    )
                    return
        command_strings = _literal_strings(node.args[0]) if node.args else []
        executable = ""
        if command_strings:
            executable = re.split(r"[\\/\s]", command_strings[0].strip().casefold())[-1]
        if executable in BLOCKED_SHELL_EXECUTABLES or self._contains_blocked_shell(command_strings):
            self.add(
                "blocked_shell_program",
                "process_execution",
                BLOCKED,
                "PowerShell·CMD 또는 시스템 명령 우회 실행을 허용하지 않습니다.",
                node,
            )
            return
        self.add(
            f"external_process:{qualified}",
            "process_execution",
            CONFIRMATION_REQUIRED,
            "외부 프로세스를 실행합니다.",
            node,
        )

    def _check_external_start(self, node, qualified):
        command_strings = _literal_strings(node)
        if self._contains_blocked_shell(command_strings):
            self.add(
                "blocked_shell_program",
                "process_execution",
                BLOCKED,
                "PowerShell·CMD 또는 스크립트 우회 실행을 허용하지 않습니다.",
                node,
            )
            return
        self.add(
            f"external_process:{qualified}",
            "process_execution",
            CONFIRMATION_REQUIRED,
            "외부 프로그램이나 파일을 실행합니다.",
            node,
        )

    @staticmethod
    def _contains_blocked_shell(values):
        for value in values:
            text = str(value or "").strip().casefold().strip('"\'')
            if any(text.endswith(suffix) for suffix in BLOCKED_SCRIPT_SUFFIXES):
                return True
            tokens = [token for token in re.split(r"[\\/\s]+", text) if token]
            if any(token in BLOCKED_SHELL_EXECUTABLES for token in tokens):
                return True
        return False

    def _check_open(self, node):
        mode_node = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode_node = keyword.value
        mode = _string_value(mode_node) if mode_node is not None else "r"
        self.has_file_read = mode is None or "r" in mode
        if mode is None or any(flag in mode for flag in "wax+"):
            self.has_file_write = True
            self.add(
                "file_open_for_write",
                "file_write",
                CONFIRMATION_REQUIRED,
                "파일을 생성·수정·덮어쓰기 모드로 엽니다.",
                node,
            )
            self._block_protected_path(node)
        if node.args and self._contains_sensitive_name(_literal_strings(node.args[0])):
            self.add(
                "sensitive_file_access",
                "credential_access",
                BLOCKED,
                "인증 정보나 JARVIS 개인 데이터 파일에 접근합니다.",
                node,
            )

    def _check_environ_call(self, node):
        name = _string_value(node.args[0]) if node.args else None
        if name and self._is_sensitive_name(name):
            self.add(
                "sensitive_environment_access",
                "credential_access",
                BLOCKED,
                "환경 변수에서 보안 정보에 접근하려고 합니다.",
                node,
            )
        else:
            self.add(
                "environment_access",
                "system_information",
                CONFIRMATION_REQUIRED,
                "프로세스 환경 변수에 접근합니다.",
                node,
            )

    def _check_getenv(self, node):
        name = _string_value(node.args[0]) if node.args else None
        if name and self._is_sensitive_name(name):
            self.add(
                "sensitive_environment_access",
                "credential_access",
                BLOCKED,
                "환경 변수에서 보안 정보에 접근하려고 합니다.",
                node,
            )
        else:
            self.add(
                "environment_access",
                "system_information",
                CONFIRMATION_REQUIRED,
                "프로세스 환경 변수에 접근합니다.",
                node,
            )

    def _block_protected_path(self, node):
        strings = _literal_strings(node.args[0]) if node.args else []
        if any(self._is_protected_path(value) for value in strings):
            self.add(
                "jarvis_self_modification",
                "self_modification",
                BLOCKED,
                "JARVIS 핵심 코드나 보안 정책 파일을 변경하려고 합니다.",
                node,
            )

    @staticmethod
    def _is_network_call(qualified):
        return _matches_prefix(qualified, {
            "aiohttp", "ftplib", "http.client", "requests", "smtplib",
            "socket", "urllib.request", "webbrowser", "websocket",
        }) and not qualified.startswith("urllib.parse.")

    @staticmethod
    def _is_ui_control_call(qualified):
        return _matches_prefix(qualified, {
            "keyboard", "mouse", "pyautogui", "pywinauto", "uiautomation",
            "win32gui",
        }) or qualified in {
            "win32api.SetCursorPos", "win32api.ShellExecute",
        }

    @staticmethod
    def _is_sensitive_api(qualified):
        return _matches_prefix(qualified, {
            "browser_cookie3", "keyring", "win32crypt.CryptUnprotectData",
        })

    @staticmethod
    def _is_sensitive_name(value):
        normalized = str(value or "").strip().casefold().replace("-", "_")
        return any(part in normalized for part in SENSITIVE_NAME_PARTS)

    @classmethod
    def _contains_sensitive_name(cls, values):
        return any(cls._is_sensitive_name(value) for value in values)

    @staticmethod
    def _is_protected_path(value):
        normalized = str(value or "").replace("\\", "/").casefold()
        return any(part in normalized for part in PROTECTED_PATH_PARTS)


class DynamicCodePreflight:
    def __init__(self, protected_root=None):
        self.protected_root = Path(protected_root or PROJECT_ROOT).resolve()

    def analyze(self, code, argument=None, context=None):
        text = str(code or "")
        code_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if len(text) > MAX_CODE_CHARACTERS:
            return DynamicCodePreflightResult(
                status=BLOCKED,
                code_sha256=code_sha256,
                findings=(RiskFinding(
                    code="code_too_large",
                    category="validation",
                    disposition=BLOCKED,
                    message="동적 코드가 안전하게 검사할 수 있는 크기를 초과했습니다.",
                ),),
                policy_version=POLICY_VERSION,
            )
        try:
            tree = ast.parse(text, filename="<jarvis-dynamic-preflight>", mode="exec")
        except SyntaxError as error:
            return DynamicCodePreflightResult(
                status=BLOCKED,
                code_sha256=code_sha256,
                findings=(RiskFinding(
                    code="syntax_error",
                    category="validation",
                    disposition=BLOCKED,
                    message=f"파이썬 문법 오류가 있습니다: {error.msg}",
                    line=error.lineno,
                ),),
                policy_version=POLICY_VERSION,
            )

        nodes = list(ast.walk(tree))
        if len(nodes) > MAX_AST_NODES:
            return DynamicCodePreflightResult(
                status=BLOCKED,
                code_sha256=code_sha256,
                findings=(RiskFinding(
                    code="code_too_complex",
                    category="validation",
                    disposition=BLOCKED,
                    message="동적 코드가 안전하게 검사할 수 있는 복잡도를 초과했습니다.",
                ),),
                policy_version=POLICY_VERSION,
            )

        aliases = self._collect_aliases(tree)
        direct_call_targets = {
            node.func for node in nodes if isinstance(node, ast.Call)
        }
        nested_reference_parts = {
            node.value for node in nodes if isinstance(node, ast.Attribute)
        }
        visitor = _RiskVisitor(
            aliases,
            direct_call_targets,
            nested_reference_parts,
        )
        visitor.visit(tree)
        literal_values = _literal_strings(tree)
        if any(
            value in BLOCKED_INTROSPECTION_ATTRIBUTES
            for value in literal_values
        ):
            visitor.add(
                "dynamic_introspection_name",
                "dynamic_execution",
                BLOCKED,
                "문자열로 Python 내부 실행 속성에 접근하려고 합니다.",
            )
        if any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) > MAX_STRING_LITERAL_CHARACTERS
            for node in nodes
        ):
            visitor.add(
                "oversized_string_payload",
                "dynamic_execution",
                BLOCKED,
                "매우 큰 문자열 페이로드는 난독화된 실행 코드일 수 있어 허용하지 않습니다.",
            )
        if visitor.has_broad_file_access and visitor.has_file_delete:
            visitor.add(
                "broad_file_delete",
                "broad_file_mutation",
                BLOCKED,
                "여러 파일을 탐색하면서 삭제하는 동작은 허용하지 않습니다.",
            )

        if (visitor.has_file_write or visitor.has_file_delete) and self._argument_targets_protected_root(argument):
            visitor.add(
                "protected_root_mutation",
                "self_modification",
                BLOCKED,
                "JARVIS 설치 또는 소스 폴더 안의 파일을 변경하려고 합니다.",
            )

        argument_strings = list(self._flatten_strings(argument))
        if (
            (visitor.has_file_read or visitor.has_file_write or visitor.has_file_delete)
            and visitor._contains_sensitive_name(argument_strings)
        ):
            visitor.add(
                "sensitive_argument_file_access",
                "credential_access",
                BLOCKED,
                "인증 정보나 JARVIS 개인 데이터 파일을 대상으로 사용하려고 합니다.",
            )
        categories = {item.category for item in visitor.findings}
        finding_codes = {item.code for item in visitor.findings}
        code_strings = literal_values
        if categories.intersection({"app_automation", "process_execution"}) and (
            visitor._contains_blocked_shell(code_strings)
            or visitor._contains_blocked_shell(argument_strings)
        ):
            visitor.add(
                "blocked_shell_program",
                "process_execution",
                BLOCKED,
                "PowerShell·CMD 또는 스크립트 우회 실행을 허용하지 않습니다.",
            )
        if "network" in categories and (
            "credential_access" in categories
            or "environment_access" in finding_codes
            or visitor._contains_sensitive_name(code_strings)
            or visitor._contains_sensitive_name(argument_strings)
        ):
            visitor.add(
                "possible_secret_exfiltration",
                "credential_access",
                BLOCKED,
                "보안 정보가 외부 네트워크로 전송될 가능성이 있어 차단했습니다.",
            )

        findings = tuple(sorted(
            visitor.findings,
            key=lambda item: (
                0 if item.disposition == BLOCKED else 1,
                item.line if item.line is not None else 10**9,
                item.code,
            ),
        ))
        status = (
            BLOCKED if any(item.disposition == BLOCKED for item in findings)
            else CONFIRMATION_REQUIRED
            if any(item.disposition == CONFIRMATION_REQUIRED for item in findings)
            else SAFE
        )
        return DynamicCodePreflightResult(
            status=status,
            code_sha256=code_sha256,
            findings=findings,
            policy_version=POLICY_VERSION,
        )

    @staticmethod
    def _collect_aliases(tree):
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name
                    else:
                        root = alias.name.split(".", 1)[0]
                        aliases[root] = root
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    local = alias.asname or alias.name
                    aliases[local] = f"{module}.{alias.name}" if module else alias.name
        return aliases

    def _argument_targets_protected_root(self, argument):
        root = os.path.normcase(str(self.protected_root))
        for value in self._flatten_strings(argument):
            candidate = str(value or "").strip().strip('"\'')
            if not candidate or not self._looks_like_path(candidate):
                continue
            try:
                path = Path(candidate).expanduser()
                if not path.is_absolute():
                    path = self.protected_root / path
                resolved = os.path.normcase(str(path.resolve(strict=False)))
            except (OSError, RuntimeError, ValueError):
                continue
            try:
                if os.path.commonpath([root, resolved]) == root:
                    return True
            except ValueError:
                continue
        return False

    @classmethod
    def _flatten_strings(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    parsed = json.loads(stripped)
                except (TypeError, ValueError):
                    pass
                else:
                    # Inspect the decoded values, not the serialized container.
                    # A JSON string containing a Windows path would otherwise be
                    # mistaken for one large relative path under PROJECT_ROOT.
                    yield from cls._flatten_strings(parsed)
                    return
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                yield from cls._flatten_strings(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from cls._flatten_strings(item)

    @staticmethod
    def _looks_like_path(value):
        return bool(
            re.match(r"^[a-zA-Z]:[\\/]", value)
            or value.startswith(("./", ".\\", "../", "..\\", "~/", "~\\"))
            or "/" in value
            or "\\" in value
        )
