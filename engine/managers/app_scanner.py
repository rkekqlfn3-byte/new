import logging
import os
import re
import winreg
import time


logger = logging.getLogger(__name__)

COMMON_APP_MAP = {
    "bandizip": "반디집",
    "anysign4pc": "애니사인",
    "4ddig file repair": "포디딕",
    "chrome": "크롬",
    "msedge": "엣지",
    "notepad": "메모장",
    "calc": "계산기",
    "cmd": "명령프롬프트",
    "excel": "엑셀",
    "powerpnt": "파워포인트",
    "winword": "워드",
    "kakao": "카카오톡",
    "kakaotalk": "카카오톡",
    "discord": "디스코드",
    "steam": "스팀",
    "obs64": "OBS",
    "spotify": "스포티파이",
    "code": "VS코드",
    "photoshop": "포토샵",
    "illustrator": "일러스트레이터",
    "premiere": "프리미어",
    "zoom": "줌",
    "slack": "슬랙",
    "notion": "노션"
}

NOISE_WORD_RE = re.compile(
    r"(?:^|[\s._-])(?:"
    r"unins(?:tall(?:er)?)?|updat(?:e|er)|setup|installer?|helper|"
    r"crash(?:pad|handler|reporter)?|reporter|service|telemetry|"
    r"diagnostic|bootstrap|maintenance|remove"
    r")(?:$|[\s._-])",
    re.IGNORECASE,
)
NOISE_KOREAN_WORDS = ("제거", "삭제", "업데이트", "설치 도우미", "충돌 보고")


def is_noise_app_candidate(name, path=""):
    """Return True for uninstallers, updaters, and other non-user-facing tools."""
    stem = os.path.splitext(os.path.basename(str(path).strip('"')))[0]
    values = (str(name).strip(), stem.strip())
    return any(
        NOISE_WORD_RE.search(value)
        or any(word in value for word in NOISE_KOREAN_WORDS)
        for value in values
        if value
    )


def _normalized_app_name(value):
    return re.sub(r"[^0-9a-z가-힣]", "", str(value).lower())


def is_likely_primary_executable(path, program_root):
    """Keep representative executables and reject arbitrary bundled helpers."""
    if is_noise_app_candidate(os.path.basename(path), path):
        return False

    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if stem in COMMON_APP_MAP:
        return True

    try:
        relative = os.path.relpath(path, program_root)
    except ValueError:
        return False
    folders = os.path.dirname(relative).split(os.sep)
    stem_clean = _normalized_app_name(stem)
    if len(stem_clean) < 3:
        return False

    for folder in folders:
        folder_clean = _normalized_app_name(folder)
        if len(folder_clean) >= 3 and (
            stem_clean in folder_clean or folder_clean in stem_clean
        ):
            return True
    return False

def scan_windows_apps(noun_dict):
    """Scans Windows Registry and Start Menu/Desktop for apps and updates noun dictionary."""
    apps_found = 0
    
    # 1. Scan Registry (App Paths)
    try:
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths")
        ]
        for hkey, subkey in reg_paths:
            try:
                key = winreg.OpenKey(hkey, subkey)
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        app_name = winreg.EnumKey(key, i)
                        app_key = winreg.OpenKey(key, app_name)
                        app_path, _ = winreg.QueryValueEx(app_key, "")
                        app_path = str(app_path).strip().strip('"')
                        clean_name = app_name.lower().replace(".exe", "")
                        clean_name = COMMON_APP_MAP.get(clean_name, clean_name)
                        if (
                            clean_name not in noun_dict
                            and app_path
                            and not is_noise_app_candidate(clean_name, app_path)
                        ):
                            noun_dict[clean_name] = app_path
                            apps_found += 1
                    except OSError:
                        continue
            except OSError:
                continue
    except Exception as error:
        logger.warning("레지스트리 앱 검색 실패: %s", error)

    # 1.5 Deep Scan Program Files for .exe
    try:
        program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
        
        for p_dir in [program_files, program_files_x86]:
            if not os.path.exists(p_dir):
                continue
            for root, dirs, files in os.walk(p_dir):
                depth = root[len(p_dir):].count(os.sep)
                if depth > 3:
                    dirs.clear() # Don't go deeper
                    continue
                
                for file in files:
                    if file.lower().endswith('.exe'):
                        clean_name = file[:-4].lower()
                        full_path = os.path.join(root, file)
                        if not is_likely_primary_executable(full_path, p_dir):
                            continue
                        clean_name = COMMON_APP_MAP.get(clean_name, clean_name)
                        if clean_name not in noun_dict:
                            noun_dict[clean_name] = full_path
                            apps_found += 1
    except Exception as error:
        logger.warning("Program Files 앱 검색 실패: %s", error)

    # 2. Scan Start Menu & Desktop for shortcuts (.lnk)
    try:
        user_profile = os.environ.get('USERPROFILE', '')
        program_data = os.environ.get('PROGRAMDATA', '')
        
        shortcut_dirs = [
            os.path.join(program_data, r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(user_profile, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
            os.path.join(user_profile, "Desktop"),
            os.path.join(user_profile, "OneDrive", "Desktop")
        ]
        
        for s_dir in shortcut_dirs:
            if not os.path.exists(s_dir):
                continue
            for root, dirs, files in os.walk(s_dir):
                for file in files:
                    if file.lower().endswith('.lnk'):
                        clean_name = file[:-4].lower()
                        clean_name = clean_name.replace(" 실행", "")
                        clean_name = COMMON_APP_MAP.get(clean_name, clean_name)
                        full_path = os.path.join(root, file)
                        if (
                            clean_name not in noun_dict
                            and not is_noise_app_candidate(clean_name, full_path)
                        ):
                            noun_dict[clean_name] = full_path
                            apps_found += 1
    except Exception as error:
        logger.warning("바로가기 앱 검색 실패: %s", error)
        
    return apps_found

def scan_recent_windows_apps(noun_dict, hours=24):
    """Scans shortcuts created within the last `hours` and adds them."""
    apps_found = 0
    cutoff_time = time.time() - (hours * 3600)
    
    try:
        user_profile = os.environ.get('USERPROFILE', '')
        program_data = os.environ.get('PROGRAMDATA', '')
        
        shortcut_dirs = [
            os.path.join(program_data, r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(user_profile, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
            os.path.join(user_profile, "Desktop"),
            os.path.join(user_profile, "OneDrive", "Desktop")
        ]
        
        for s_dir in shortcut_dirs:
            if not os.path.exists(s_dir):
                continue
            for root, dirs, files in os.walk(s_dir):
                for file in files:
                    if file.lower().endswith('.lnk'):
                        full_path = os.path.join(root, file)
                        if os.path.getctime(full_path) > cutoff_time or os.path.getmtime(full_path) > cutoff_time:
                            clean_name = file[:-4].lower().replace(" 실행", "")
                            clean_name = COMMON_APP_MAP.get(clean_name, clean_name)
                            if (
                                clean_name not in noun_dict
                                and not is_noise_app_candidate(clean_name, full_path)
                            ):
                                noun_dict[clean_name] = full_path
                                apps_found += 1
    except Exception as error:
        logger.warning("최근 앱 검색 실패: %s", error)
        
    return apps_found
