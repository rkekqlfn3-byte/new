import logging
import os
import subprocess
import urllib.parse
import ctypes
import re
import shlex
import time
from datetime import datetime
import psutil
import win32gui
import win32process
import win32con
from engine.hotkeys import press_hotkey
from engine.execution_result import failure_result, normalize_execution_result, success_result
from engine.local_commands.local_command_analyzer import (
    extract_volume_adjustment,
    extract_volume_percent,
)
from engine.security.launch_policy import (
    BLOCKED_EXECUTABLE_STEMS,
    BLOCKED_LAUNCH_EXTENSIONS,
    UnsafeLaunchTarget,
    validate_launch_target,
)
from engine.system_volume import (
    SystemVolumeError,
    adjust_system_volume,
    set_system_muted,
    set_system_volume,
)


logger = logging.getLogger(__name__)


class BuiltinMacros:
    _BLOCKED_COMMAND_EXECUTABLES = BLOCKED_EXECUTABLE_STEMS
    _BLOCKED_SCRIPT_EXTENSIONS = BLOCKED_LAUNCH_EXTENSIONS
    _SHELL_METACHARACTERS = re.compile(r"[&|<>^\r\n]")

    def __init__(self, dict_mgr, action_executor):
        self.dict_mgr = dict_mgr
        self.action_executor = action_executor

    def execute_hotkey(self, macro_data):
        try:
            keys = [k.strip().lower() for k in macro_data["data"].split("+")]
            press_hotkey(keys)
            return success_result(
                f"단축키 '{macro_data['data']}'을(를) 실행했습니다.",
                action="hotkey", target=macro_data["data"], verified=False,
            )
        except (KeyError, AttributeError, TypeError, ValueError, OSError) as e:
            return failure_result(
                f"단축키 실행 중 오류가 발생했습니다: {e}",
                action="hotkey", target=macro_data.get("data"),
                error_type="execution_error",
            )

    @staticmethod
    def _split_command_line(command):
        if os.name != "nt":
            return shlex.split(command)

        argc = ctypes.c_int()
        command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
        command_line_to_argv.argtypes = [
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int),
        ]
        command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argv_pointer = command_line_to_argv(command, ctypes.byref(argc))
        if not argv_pointer:
            raise ValueError("프로그램 실행 인수를 해석할 수 없습니다.")
        try:
            return [argv_pointer[index] for index in range(argc.value)]
        finally:
            local_free = ctypes.windll.kernel32.LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(ctypes.cast(argv_pointer, ctypes.c_void_p))

    @classmethod
    def validate_command(cls, value):
        command = str(value or "").strip()
        if not command:
            raise ValueError("실행할 프로그램이 비어 있습니다.")
        if len(command) > 1000 or "\x00" in command:
            raise ValueError("프로그램 실행 문자열이 올바르지 않습니다.")
        if cls._SHELL_METACHARACTERS.search(command):
            raise ValueError(
                "연결·리디렉션 문자가 포함된 시스템 명령은 실행할 수 없습니다."
            )

        arguments = cls._split_command_line(command)
        if not arguments or not str(arguments[0]).strip():
            raise ValueError("실행할 프로그램을 찾을 수 없습니다.")
        executable = os.path.expandvars(str(arguments[0]).strip().strip('"'))
        executable_name = os.path.basename(executable).casefold()
        stem, extension = os.path.splitext(executable_name)
        if stem in cls._BLOCKED_COMMAND_EXECUTABLES:
            raise ValueError(
                f"보안상 '{executable_name}' 시스템 명령은 매크로로 실행할 수 없습니다."
            )
        if extension in cls._BLOCKED_SCRIPT_EXTENSIONS:
            raise ValueError(
                f"보안상 '{extension}' 스크립트는 매크로로 실행할 수 없습니다."
            )
        arguments[0] = executable
        return arguments

    def execute_cmd(self, macro_data, approved=False):
        if not approved:
            return failure_result(
                "프로그램 실행 확인을 거치지 않아 매크로를 차단했습니다.",
                action="command_line", target=macro_data.get("data"),
                error_type="validation_error", status="blocked",
            )
        try:
            arguments = self.validate_command(macro_data.get("data"))
            subprocess.Popen(arguments, shell=False)
            return success_result(
                f"프로그램 '{macro_data['data']}'을(를) 실행했습니다.",
                action="command_line", target=macro_data["data"], verified=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            return failure_result(
                f"명령어 실행 중 오류가 발생했습니다: {e}",
                action="command_line", target=macro_data.get("data"),
                error_type="execution_error",
            )

    def handle_open(self, user_input, tokens, app_name, app_path, log_callback):
        if app_path:
            try:
                clean_path = validate_launch_target(app_path, app_name)
                if log_callback: log_callback(f"[Execution] {clean_path} 실행 중...")
                os.startfile(clean_path)
                focused = False
                if not clean_path.casefold().startswith(("http://", "https://")):
                    self.action_executor.focus_window_when_ready(app_name)
                    focused = True
                return success_result(
                    f"'{app_name}'을(를) 실행하고 맨 앞으로 가져왔습니다."
                    if focused else f"'{app_name}'을(를) 실행했습니다.",
                    action="open_app", target=app_name, verified=focused,
                )
            except UnsafeLaunchTarget as e:
                return failure_result(
                    str(e),
                    action="open_app", target=app_name,
                    error_type="validation_error", retryable=False,
                    status="blocked",
                )
            except Exception as e:
                return failure_result(
                    f"실행 중 오류가 발생했습니다: {e}",
                    action="open_app", target=app_name,
                    error_type="execution_error", retryable=False,
                )
        return failure_result(
            "열 앱을 사전에서 찾지 못했습니다. 앱 스캔을 먼저 실행해 보세요.",
            action="open_app", target=app_name, error_type="target_not_found",
        )

    def handle_close(self, user_input, tokens, app_name, app_path, log_callback):
        if not app_name or not app_path:
            return failure_result(
                "닫을 대상을 알 수 없습니다. 대상 이름을 정확히 말씀해 주세요.",
                action="close_app", target=app_name, error_type="target_not_found",
            )

        if app_path.startswith("http"):
            return failure_result(
                "웹사이트는 탭 단위로 닫아야 해서 직접 종료할 수 없습니다. 브라우저에서 직접 닫아 주세요.",
                action="close_app", target=app_name, error_type="validation_error",
            )

        target_exe_name = ""
        if app_path.lower().endswith(".lnk"):
            try:
                import win32com.client
                shell = win32com.client.Dispatch("WScript.Shell")
                shortcut = shell.CreateShortCut(app_path)
                resolved_path = shortcut.Targetpath
                if resolved_path:
                    target_exe_name = os.path.basename(resolved_path).lower().replace(".exe", "")
                else:
                    target_exe_name = app_name
            except Exception as error:
                logger.debug("바로가기 대상을 확인하지 못했습니다: %s", error)
                target_exe_name = app_name
        else:
            target_exe_name = os.path.basename(app_path).lower().replace(".exe", "")

        target_pids = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                proc_name = proc.info['name'].lower()
                if target_exe_name in proc_name or proc_name.replace('.exe', '') == target_exe_name:
                    target_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not target_pids:
            return failure_result(
                f"실행 중인 '{app_name}'을(를) 찾지 못했습니다. 이미 종료된 것 같습니다.",
                action="close_app", target=app_name, error_type="target_not_found",
            )

        target_hwnds = []
        def enum_window_callback(hwnd, pids):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids and win32gui.IsWindowVisible(hwnd):
                target_hwnds.append(hwnd)

        win32gui.EnumWindows(enum_window_callback, target_pids)

        if not target_hwnds:
            failed_pids = []
            for pid in target_pids:
                try:
                    psutil.Process(pid).kill()
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, OSError) as error:
                    failed_pids.append(pid)
                    logger.debug(
                        "백그라운드 프로세스 종료 실패 pid=%s: %s", pid, error
                    )
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if not any(psutil.pid_exists(pid) for pid in target_pids):
                    break
                time.sleep(0.1)
            failed_pids.extend(
                pid for pid in target_pids if psutil.pid_exists(pid)
            )
            failed_pids = sorted(set(failed_pids))
            if failed_pids:
                return failure_result(
                    "창이 없는 백그라운드 프로세스를 종료하지 못했습니다.",
                    action="close_app",
                    target=app_name,
                    error_type="environment_error",
                    retryable=False,
                    data={"failed_pids": failed_pids},
                )
            return success_result(
                "창이 없는 백그라운드 프로세스라 강제로 종료했습니다.",
                action="close_app", target=app_name, verified=True,
            )

        for hwnd in target_hwnds:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

        if log_callback: log_callback("[Execution] 종료 시그널 전송... 확인 중")

        start_time = time.time()
        while time.time() - start_time < 3.0:
            if not any(psutil.pid_exists(pid) for pid in target_pids):
                return success_result(
                    f"'{app_name}'을(를) 종료했습니다.",
                    action="close_app", target=app_name, verified=True,
                )
            time.sleep(0.5)

        return failure_result(
            "저장되지 않은 작업이 있어 종료를 대기 중입니다. 직접 확인해 주세요.",
            action="close_app", target=app_name,
            error_type="environment_error", retryable=False,
        )

    def handle_search(self, user_input, tokens, app_name, app_path, log_callback):
        matched_engine, engine_url, search_query = self.extract_search_request(user_input)

        if search_query:
            final_url = engine_url + urllib.parse.quote(search_query)
            os.startfile(final_url)
            return success_result(
                f"{matched_engine}에서 '{search_query}'을(를) 검색합니다.",
                action="search", target=search_query, verified=False,
            )
        return failure_result(
            "무엇을 검색할지 함께 말씀해 주세요.",
            action="search", error_type="validation_error",
        )

    def extract_search_request(self, user_input):
        matched_engine = "구글"
        engine_url = self.dict_mgr.search_engines_dict.get("구글", "https://www.google.com/search?q=")

        for engine, url in self.dict_mgr.search_engines_dict.items():
            if engine in user_input:
                matched_engine = engine
                engine_url = url
                break

        search_query = user_input.strip()
        engine_pattern = rf"{re.escape(matched_engine)}(?:에서|으로|은|는|이|가|을|를|로|에)?"
        search_query = re.sub(engine_pattern, " ", search_query, count=1)

        action_pattern = r"(?:검색해줘|검색해|검색|찾아줘|찾아|알아봐줘|알아봐|쳐줘|쳐|알려줘)"
        search_query = re.sub(rf"^\s*{action_pattern}\s+", "", search_query)
        search_query = re.sub(rf"\s*{action_pattern}\s*[.!?]*$", "", search_query)
        search_query = re.sub(r"\s+", " ", search_query).strip(" .,!?")
        return matched_engine, engine_url, search_query

    def handle_playpause(self, *args):
        ctypes.windll.user32.keybd_event(0xB3, 0, 0, 0)
        return success_result("미디어 재생/일시정지를 실행했습니다.", action="play_pause", verified=False)

    def _handle_volume_adjustment(self, user_input, direction):
        parsed = extract_volume_adjustment(user_input)
        amount = parsed[1] if parsed and parsed[0] == direction else 10
        action = "volume_up" if direction == "up" else "volume_down"
        if not 1 <= amount <= 100:
            return failure_result(
                "볼륨은 한 번에 1부터 100까지만 올리거나 내릴 수 있습니다.",
                action=action,
                target=amount,
                error_type="validation_error",
                status="blocked",
            )
        delta = amount if direction == "up" else -amount
        try:
            before, applied = adjust_system_volume(delta)
        except (OSError, ValueError, SystemVolumeError) as error:
            return failure_result(
                str(error),
                action=action,
                target=amount,
                error_type="environment_error",
                retryable=False,
            )

        if before == applied:
            message = (
                "시스템 볼륨이 이미 최대입니다."
                if direction == "up" else "시스템 볼륨이 이미 최소입니다."
            )
        else:
            verb = "올렸습니다" if direction == "up" else "내렸습니다"
            message = f"시스템 볼륨을 {before}%에서 {applied}%로 {verb}."
        return success_result(
            message,
            action=action,
            target=applied,
            verified=True,
            data={"before": before, "requested_delta": delta, "applied": applied},
        )

    def handle_vol_up(self, user_input="", *args):
        return self._handle_volume_adjustment(user_input, "up")

    def handle_vol_down(self, user_input="", *args):
        return self._handle_volume_adjustment(user_input, "down")

    def handle_vol_set(self, user_input, *args):
        percent = extract_volume_percent(user_input)
        if percent is None:
            return failure_result(
                "설정할 볼륨을 0~100 사이의 숫자로 말씀해 주세요.",
                action="volume_set", error_type="validation_error",
            )
        if not 0 <= percent <= 100:
            return failure_result(
                "볼륨은 0부터 100 사이로만 설정할 수 있습니다.",
                action="volume_set", target=percent,
                error_type="validation_error", status="blocked",
            )
        try:
            applied = set_system_volume(percent)
        except (OSError, ValueError, SystemVolumeError) as error:
            return failure_result(
                str(error), action="volume_set", target=percent,
                error_type="environment_error", retryable=False,
            )
        return success_result(
            f"시스템 볼륨을 {applied}%로 설정했습니다.",
            action="volume_set", target=applied, verified=True,
        )

    def handle_mute(self, user_input, *args):
        compact = re.sub(r"\s+", "", str(user_input or "").casefold())
        unmute = any(marker in compact for marker in (
            "음소거해제", "음소거풀", "소리켜", "소리다시켜", "소리복구",
        ))
        muted = not unmute
        try:
            applied = set_system_muted(muted)
        except (OSError, SystemVolumeError) as error:
            return failure_result(
                str(error), action="unmute" if unmute else "mute",
                error_type="environment_error", retryable=False,
            )
        if applied != muted:
            return failure_result(
                "Windows 음소거 상태가 요청한 값으로 바뀌지 않았습니다.",
                action="unmute" if unmute else "mute",
                error_type="verification_error", retryable=False,
            )
        return success_result(
            "음소거를 해제했습니다." if unmute else "음소거했습니다.",
            action="unmute" if unmute else "mute", verified=True,
        )

    @staticmethod
    def _run_shutdown_command(arguments, *, action, success_message):
        try:
            completed = subprocess.run(
                ["shutdown.exe", *arguments],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning("Windows 종료 명령 실행 실패 action=%s: %s", action, error)
            return failure_result(
                f"Windows 종료 명령을 실행하지 못했습니다: {error}",
                action=action,
                error_type="environment_error",
                retryable=False,
            )

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            logger.warning(
                "Windows 종료 명령 거부 action=%s code=%s detail=%s",
                action,
                completed.returncode,
                detail,
            )
            message = "Windows가 종료 명령을 처리하지 못했습니다."
            if detail:
                message = f"{message} {detail}"
            return failure_result(
                message,
                action=action,
                error_type="execution_error",
                retryable=False,
                data={"returncode": completed.returncode},
            )

        return success_result(success_message, action=action, verified=True)

    def handle_shutdown(self, *args):
        return self._run_shutdown_command(
            ["/s", "/t", "60"],
            action="shutdown",
            success_message=(
                "60초 뒤에 컴퓨터가 종료되도록 예약했습니다. "
                "취소하려면 '취소해'라고 말씀해 주세요."
            ),
        )

    def handle_cancel_shutdown(self, *args):
        return self._run_shutdown_command(
            ["/a"],
            action="cancel_shutdown",
            success_message="종료 예약을 취소했습니다.",
        )

    def handle_time(self, *args):
        now = datetime.now()
        ampm = "오후" if now.hour >= 12 else "오전"
        hour = now.hour if now.hour <= 12 else now.hour - 12
        if hour == 0: hour = 12
        return success_result(
            f"지금은 {ampm} {hour}시 {now.minute}분입니다.",
            action="time", verified=True,
        )

    def handle_date(self, *args):
        """Return today's date from the local system clock, without an AI call."""
        now = datetime.now()
        weekday_names = (
            "월요일", "화요일", "수요일", "목요일",
            "금요일", "토요일", "일요일",
        )
        return success_result(
            f"오늘은 {now.year}년 {now.month}월 {now.day}일 "
            f"{weekday_names[now.weekday()]}입니다.",
            action="date", verified=True,
        )

    def handle_weather(self, *args):
        url = "https://search.naver.com/search.naver?query=오늘날씨"
        os.startfile(url)
        return success_result(
            "오늘 날씨를 네이버에서 열었습니다.",
            action="weather", target=url, verified=False,
        )
