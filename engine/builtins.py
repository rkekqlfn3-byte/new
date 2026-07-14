import os
import subprocess
import urllib.parse
import ctypes
import re
import time
from datetime import datetime
import psutil
import win32gui
import win32process
import win32con
from engine.hotkeys import press_hotkey
from engine.execution_result import failure_result, normalize_execution_result, success_result

class BuiltinMacros:
    def __init__(self, dict_mgr, parser):
        self.dict_mgr = dict_mgr
        self.parser = parser

    def execute_hotkey(self, macro_data):
        try:
            keys = [k.strip().lower() for k in macro_data["data"].split("+")]
            press_hotkey(keys)
            return success_result(
                f"단축키 '{macro_data['data']}'(을)를 실행했어! ⚡",
                action="hotkey", target=macro_data["data"], verified=False,
            )
        except Exception as e:
            return failure_result(
                f"단축키 실행 중 오류가 발생했어 ㅠㅠ: {e}",
                action="hotkey", target=macro_data.get("data"),
                error_type="execution_error",
            )

    def execute_cmd(self, macro_data):
        try:
            subprocess.Popen(macro_data["data"], shell=True)
            return success_result(
                f"명령어 '{macro_data['data']}'(을)를 실행했어! 💻",
                action="command_line", target=macro_data["data"], verified=False,
            )
        except Exception as e:
            return failure_result(
                f"명령어 실행 중 오류가 발생했어 ㅠㅠ: {e}",
                action="command_line", target=macro_data.get("data"),
                error_type="execution_error",
            )

    def execute_compound(self, macro_data, log_callback, image_data, mode):
        commands = [c.strip() for c in macro_data["data"].split(",")]
        completed = []
        for index, cmd in enumerate(commands, start=1):
            result = self.parser.execute_command_result(
                cmd, log_callback, image_data, mode
            )
            completed.append(result)
            if not result["success"]:
                return failure_result(
                    f"연속 동작 {index}단계에서 중단했습니다: {result['message']}",
                    action="compound", error_type=result.get("error_type", "execution_error"),
                    failed_step=index, retryable=result.get("retryable", False),
                    data={"steps": completed},
                )
        return success_result(
            "연속 동작을 모두 수행했어! ✨", action="compound",
            verified=all(item.get("verified") for item in completed),
            data={"steps": completed},
        )

    def handle_open(self, user_input, tokens, app_name, app_path, log_callback):
        if app_path:
            try:
                clean_path = app_path.replace('"', '')
                if log_callback: log_callback(f"[Execution] {clean_path} 실행 중...")
                os.startfile(clean_path)
                return success_result(
                    f"짜잔~ '{app_name}' 열어줬어! 또 필요한 거 있으면 말해줘! ✨",
                    action="open_app", target=app_name, verified=False,
                )
            except Exception as e:
                return failure_result(
                    f"으앙... 실행하다 에러 났어 ㅠㅠ ({e})",
                    action="open_app", target=app_name,
                    error_type="execution_error", retryable=False,
                )
        return failure_result(
            "에엣? 어떤 앱을 열어야 할지 사전에서 못 찾겠어... 앱스캔 한 번 해볼래? 🥺",
            action="open_app", target=app_name, error_type="target_not_found",
        )

    def handle_close(self, user_input, tokens, app_name, app_path, log_callback):
        if not app_name or not app_path:
            return failure_result(
                "무엇을 닫아야 할지 모르겠어 ㅠㅠ 대상 이름을 정확히 말해줄래?",
                action="close_app", target=app_name, error_type="target_not_found",
            )
            
        if app_path.startswith("http"):
            return failure_result(
                "앗! 웹사이트는 탭을 닫아야 해서 내가 직접 꺼줄 수 없어 ㅠㅠ 브라우저를 직접 닫아줘!",
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
            except Exception as e:
                print(f"Error resolving lnk: {e}")
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
                f"어라? 실행 중인 '{app_name}'(이)가 안 보이는데? 이미 종료된 거 아닐까? 🤔",
                action="close_app", target=app_name, error_type="target_not_found",
            )
            
        target_hwnds = []
        def enum_window_callback(hwnd, pids):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in pids and win32gui.IsWindowVisible(hwnd):
                target_hwnds.append(hwnd)
        
        win32gui.EnumWindows(enum_window_callback, target_pids)
        
        if not target_hwnds:
            for pid in target_pids:
                try: psutil.Process(pid).kill()
                except: pass
            return success_result(
                "창이 없는 백그라운드 프로세스라 강제로 닫았어! ✨",
                action="close_app", target=app_name, verified=True,
            )
            
        for hwnd in target_hwnds:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            
        if log_callback: log_callback("[Execution] 우아한 종료 시그널 전송... 확인 중 ⏳")
        
        start_time = time.time()
        while time.time() - start_time < 3.0:
            if not any(psutil.pid_exists(pid) for pid in target_pids):
                return success_result(
                    f"응응! '{app_name}' 깔끔하게 닫았어! ✨",
                    action="close_app", target=app_name, verified=True,
                )
            time.sleep(0.5)
            
        return failure_result(
            "저장되지 않은 작업이 있어 종료를 대기 중입니다. 직접 확인해 주세요. 🚨",
            action="close_app", target=app_name,
            error_type="environment_error", retryable=False,
        )

    def handle_search(self, user_input, tokens, app_name, app_path, log_callback):
        matched_engine, engine_url, search_query = self.extract_search_request(user_input)

        if search_query:
            final_url = engine_url + urllib.parse.quote(search_query)
            os.startfile(final_url)
            return success_result(
                f"{matched_engine}에서 '{search_query}'(을)를 검색할게! 🔍",
                action="search", target=search_query, verified=False,
            )
        return failure_result(
            "에엣? 무엇을 검색할지 내용을 같이 말해줄래? 🤔",
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
        return success_result("미디어 재생/일시정지 완료! 🎵", action="play_pause", verified=False)

    def handle_vol_up(self, *args):
        for _ in range(5): ctypes.windll.user32.keybd_event(0xAF, 0, 0, 0)
        return success_result("소리를 조금 키워줄게! 🔊", action="volume_up", verified=False)

    def handle_vol_down(self, *args):
        for _ in range(5): ctypes.windll.user32.keybd_event(0xAE, 0, 0, 0)
        return success_result("소리를 조금 줄여줄게! 🔉", action="volume_down", verified=False)

    def handle_mute(self, *args):
        ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
        return success_result("소리 설정(음소거)을 변경했어! 🔇", action="mute", verified=False)

    def handle_shutdown(self, *args):
        os.system("shutdown /s /t 60")
        return success_result(
            "앗! 60초 뒤에 컴퓨터가 꺼지도록 예약했어! 취소하려면 '취소해'라고 말해줘! 😱",
            action="shutdown", verified=False,
        )

    def handle_cancel_shutdown(self, *args):
        os.system("shutdown /a")
        return success_result("휴! 다행이다. 종료 예약을 안전하게 취소했어! 😮‍💨", action="cancel_shutdown", verified=False)

    def handle_time(self, *args):
        now = datetime.now()
        ampm = "오후" if now.hour >= 12 else "오전"
        hour = now.hour if now.hour <= 12 else now.hour - 12
        if hour == 0: hour = 12
        return success_result(
            f"지금은 {ampm} {hour}시 {now.minute}분이야! ⏰ 시간 참 빠르다 그치?",
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
            "오늘 날씨를 네이버에서 찾아봤어! 우산 챙기는 거 잊지 마! ☂️☀️",
            action="weather", target=url, verified=False,
        )
