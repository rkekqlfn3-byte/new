import logging
import os
import re
import time
import winreg

from engine.security.launch_policy import is_safe_launch_target

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


def matches_requested_app_candidate(name, path, candidates):
    """Return whether one discovered app is a bounded target match.

    Discovery uses exact normalized names and the built-in common aliases.
    The normal parser may apply typo tolerance after registration, but the
    discovery pass itself must not register an unrelated program.
    """
    requested = {
        _normalized_app_name(item)
        for item in (candidates or ())
        if _normalized_app_name(item)
    }
    if not requested:
        return False
    stem = os.path.splitext(os.path.basename(str(path or "").strip('"')))[0]
    mapped_stem = COMMON_APP_MAP.get(stem.casefold(), stem)
    discovered = {
        _normalized_app_name(name),
        _normalized_app_name(stem),
        _normalized_app_name(mapped_stem),
    }
    return bool(requested.intersection(item for item in discovered if item))


def scan_matching_windows_apps(noun_dict, candidates):
    """Discover only explicitly requested apps without a deep disk scan.

    Registry App Paths and Start Menu/Desktop shortcuts are bounded indexes.
    Program Files recursion is intentionally excluded so a missed target
    cannot make an ordinary command stall for a long time. Every added target
    still passes the central launch policy.
    """
    requested = {
        _normalized_app_name(item)
        for item in (candidates or ())
        if _normalized_app_name(item)
    }
    if not requested:
        return 0

    apps_found = 0

    def add_candidate(name, path):
        nonlocal apps_found
        clean_path = str(path or "").strip().strip('"')
        clean_name = str(name or "").strip().casefold()
        if not clean_path or not clean_name:
            return
        stem = os.path.splitext(os.path.basename(clean_path))[0].casefold()
        clean_name = COMMON_APP_MAP.get(stem, clean_name)
        if (
            clean_name in noun_dict
            or not matches_requested_app_candidate(clean_name, clean_path, requested)
            or is_noise_app_candidate(clean_name, clean_path)
            or not is_safe_launch_target(clean_path, clean_name)
        ):
            return
        noun_dict[clean_name] = clean_path
        apps_found += 1

    reg_paths = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
    )
    for hkey, subkey in reg_paths:
        key = None
        try:
            key = winreg.OpenKey(hkey, subkey)
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    app_name = winreg.EnumKey(key, index)
                    if not matches_requested_app_candidate(app_name, app_name, requested):
                        continue
                    app_key = winreg.OpenKey(key, app_name)
                    try:
                        app_path, _ = winreg.QueryValueEx(app_key, "")
                    finally:
                        try:
                            winreg.CloseKey(app_key)
                        except OSError:
                            pass
                    add_candidate(app_name, app_path)
                except OSError:
                    continue
        except OSError:
            continue
        finally:
            if key is not None:
                try:
                    winreg.CloseKey(key)
                except OSError:
                    pass

    user_profile = os.environ.get("USERPROFILE", "")
    program_data = os.environ.get("PROGRAMDATA", "")
    shortcut_dirs = (
        os.path.join(program_data, r"Microsoft\Windows\Start Menu\Programs"),
        os.path.join(user_profile, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs"),
        os.path.join(user_profile, "Desktop"),
        os.path.join(user_profile, "OneDrive", "Desktop"),
    )
    for shortcut_dir in shortcut_dirs:
        if not os.path.isdir(shortcut_dir):
            continue
        try:
            for root, _dirs, files in os.walk(shortcut_dir):
                for file_name in files:
                    if not file_name.casefold().endswith(".lnk"):
                        continue
                    display_name = file_name[:-4].replace(" 바로 가기", "").strip()
                    full_path = os.path.join(root, file_name)
                    if matches_requested_app_candidate(display_name, full_path, requested):
                        add_candidate(display_name, full_path)
        except OSError as error:
            logger.debug("Bounded shortcut discovery failed for %s: %s", shortcut_dir, error)

    return apps_found


def is_likely_primary_executable(path, program_root):
    """Keep representative executables and reject arbitrary bundled helpers."""
    if (
        is_noise_app_candidate(os.path.basename(path), path)
        or not is_safe_launch_target(path, os.path.basename(path))
    ):
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
                            and is_safe_launch_target(app_path, clean_name)
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
                            and is_safe_launch_target(full_path, clean_name)
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
                                and is_safe_launch_target(full_path, clean_name)
                            ):
                                noun_dict[clean_name] = full_path
                                apps_found += 1
    except Exception as error:
        logger.warning("최근 앱 검색 실패: %s", error)
        
    return apps_found
