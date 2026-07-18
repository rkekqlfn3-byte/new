"""Analyze local commands without executing external actions."""

import difflib
import os
import re


_VOLUME_PERCENT_RE = re.compile(
    r"(?:시스템\s*)?(?:볼륨|소리)(?:을|를|은|는)?\s*"
    r"(?:크기(?:를)?\s*)?[\s:=]*(-?\d{1,3})(?!\d)\s*(?:%|퍼센트|프로)?"
)

_VOLUME_NUMBER_RE = re.compile(r"(-?\d{1,3})(?!\d)")
_VOLUME_TARGET_RE = re.compile(
    r"-?\d{1,3}(?!\d)\s*(?:%|퍼센트|프로)?\s*(?:으로|로)"
)
_VOLUME_UP_MARKERS = ("올려", "올리", "높여", "높이", "키워", "키우", "크게")
_VOLUME_DOWN_MARKERS = ("내려", "내리", "낮춰", "낮추", "줄여", "줄이", "작게")
_VOLUME_SMALL_MARKERS = ("조금만", "조금", "살짝", "약간", "한칸", "한 칸")


def extract_volume_percent(text):
    """Extract an explicit master-volume percentage from a Korean request."""
    match = _VOLUME_PERCENT_RE.search(str(text or "").casefold())
    return int(match.group(1)) if match else None


def extract_volume_adjustment(text):
    """Return ``(direction, amount)`` for a relative volume request.

    Korean requests such as ``볼륨 10만큼 내려줘`` contain a number but do
    not describe an absolute target.  A target marker (``30으로``) or an
    explicit setting verb keeps the request on the absolute-volume path.
    """
    lowered = str(text or "").casefold()
    compact = re.sub(r"\s+", "", lowered)
    if not any(subject in compact for subject in ("볼륨", "소리")):
        return None

    direction = None
    if any(marker in compact for marker in _VOLUME_UP_MARKERS):
        direction = "up"
    elif any(marker in compact for marker in _VOLUME_DOWN_MARKERS):
        direction = "down"
    if direction is None:
        return None

    if (
        any(marker in compact for marker in ("설정", "맞춰", "맞추"))
        or _VOLUME_TARGET_RE.search(lowered)
    ):
        return None

    number_match = _VOLUME_NUMBER_RE.search(lowered)
    if number_match:
        amount = abs(int(number_match.group(1)))
    elif any(marker in compact for marker in _VOLUME_SMALL_MARKERS):
        amount = 5
    else:
        amount = 10
    return direction, amount


COMMAND_SUFFIXES = tuple(sorted({
    "실행해줘", "검색해줘", "알아봐줘", "알려줘", "닫아줘", "열어줘", "찾아줘",
    "실행해", "검색해", "해줘", "켜줘", "쳐줘", "찾아", "검색",
    "알아봐", "열어", "닫아", "실행", "켜", "꺼", "쳐"
}, key=len, reverse=True))
APP_PARTICLES = ("에서", "으로", "은", "는", "이", "가", "을", "를", "로", "에")
APP_FILLER_SUFFIXES = ("좀", "부탁")
APP_CLEAN_RE = re.compile(r"[^0-9a-zA-Z가-힣]")


class LocalCommandAnalyzer:
    """Own local parsing rules and app-name matching caches.

    The owner is a ``CommandParser``. Keeping the reference avoids duplicating
    mutable dictionaries while keeping parsing policy out of the main command
    execution coordinator.
    """

    def __init__(self, owner):
        self.owner = owner
        self._app_index_signature = None
        self._exact_app_index = {}
        self._basename_app_index = {}
        self._substring_app_entries = []
        self._fuzzy_app_buckets = {}
        self._fuzzy_app_cache = {}

    @property
    def dict_mgr(self):
        return self.owner.dict_mgr

    def normalize_text(self, text):
        tokens = re.sub(r"[\n\t,!?]+", " ", text.lower()).split()
        normalized = []
        for token in tokens:
            clean_token = token.strip(".·-_()[]{}\"'")
            for suffix in COMMAND_SUFFIXES:
                if clean_token == suffix:
                    clean_token = ""
                    break
                if clean_token.endswith(suffix):
                    clean_token = clean_token[:-len(suffix)]
                    break
            if clean_token:
                normalized.append(clean_token)
        return normalized

    def analyze_command(self, user_input):
        """Analyze a command without executing apps, keys, processes, or AI actions."""
        if isinstance(user_input, list):
            user_input = user_input[-1].get("content", "") if user_input else ""
        text = str(user_input or "").lower().strip()

        # A learned template may contain connective words such as "열고" or
        # "하고" as part of one reusable action. It therefore wins over the
        # generic compound-command splitter.
        full_template_match = self.owner.template_matcher.match(
            text,
            getattr(self.dict_mgr, "learned_macros", {}),
            self.dict_mgr.noun_dict,
        )
        if full_template_match:
            return self.analyze_single_command(
                text, template_match=full_template_match
            )

        parts = self.split_compound_command(text)
        if len(parts) > 1:
            steps = [self.analyze_single_command(part) for part in parts]
            return {
                "kind": "compound",
                "text": text,
                "steps": steps,
                "recognized": all(step["recognized"] for step in steps),
                "executable": all(step["executable"] for step in steps),
            }
        return self.analyze_single_command(text)

    def analyze_single_command(self, text, template_match=None):
        tokens = self.normalize_text(text)
        volume_adjustment = extract_volume_adjustment(text)
        volume_percent = extract_volume_percent(text)
        if volume_adjustment is not None:
            # A relative verb wins over a bare number: ``10만큼 내려줘`` is
            # a delta, while ``30으로 맞춰줘`` remains an absolute setting.
            template_match = None
            macro = "VOL_UP" if volume_adjustment[0] == "up" else "VOL_DOWN"
        elif volume_percent is not None:
            # Deterministic local system controls take precedence over learned
            # or generated Python actions for the same utterance.
            template_match = None
            macro = "VOL_SET"
        elif template_match is None:
            template_match = self.owner.template_matcher.match(
                text,
                getattr(self.dict_mgr, "learned_macros", {}),
                self.dict_mgr.noun_dict,
            )
            macro = template_match["macro"] if template_match else self.identify_macro(text)
        else:
            macro = template_match["macro"]
        # "폴더에서 파일 찾아줘" describes a local filesystem task, not a
        # web search.  Keep explicit search-engine requests intact and hand
        # local-file searches to the AI/action-plan path instead of opening a
        # browser with a misleading query.
        if macro == "SEARCH" and self.looks_like_local_file_search(text):
            macro = None
        app_name, app_path = self.identify_app(text, tokens, None, macro)
        if template_match:
            captured_app = template_match.get("slots", {}).get("app")
            if captured_app and captured_app in self.dict_mgr.noun_dict:
                app_name = captured_app
                app_path = self.dict_mgr.noun_dict[captured_app]

        implicit_search = False
        if not macro and self.looks_like_implicit_search(text):
            macro = "SEARCH"
            implicit_search = True

        recognized = bool(macro and macro in self.dict_mgr.macro_dict)
        executable = recognized
        reason = ""
        search_engine = None
        search_query = None

        if not recognized:
            executable = False
            reason = "등록된 로컬 명령을 찾지 못했습니다."
        elif macro in {"OPEN", "CLOSE"} and (not app_name or not app_path):
            executable = False
            reason = "대상 앱을 찾지 못했습니다."
        elif macro == "SEARCH":
            search_engine, _, search_query = self.owner.builtins.extract_search_request(text)
            executable = bool(search_query)
            if not executable:
                reason = "검색어가 비어 있습니다."
        else:
            macro_data = self.dict_mgr.macro_dict[macro]
            macro_type = macro_data.get("type", "default")
            if macro_type in {"hotkey", "cmd", "compound"} and not macro_data.get("data"):
                executable = False
                reason = "매크로 실행 값이 비어 있습니다."
            elif macro_type == "learned":
                learned = self.owner._get_learned_macro(macro_data.get("app"), macro)
                executable = bool(
                    learned and (learned.get("code") or learned.get("plan"))
                )
                if not executable:
                    reason = "학습 매크로 코드를 찾지 못했습니다."
            elif macro_type == "default" and macro not in self.owner._handlers:
                executable = False
                reason = "로컬 실행 함수를 찾지 못했습니다."

        return {
            "kind": "single",
            "text": text,
            "tokens": tokens,
            "macro": macro,
            "app_name": app_name,
            "app_path": app_path,
            "implicit_search": implicit_search,
            "search_engine": search_engine,
            "search_query": search_query,
            "recognized": recognized,
            "executable": executable,
            "reason": reason,
            "template_match": template_match,
        }

    def resolve_registered_app(self, target):
        if not isinstance(target, str) or not target.strip():
            return None
        cleaned = target.strip().strip("\"'")
        if cleaned in self.dict_mgr.noun_dict:
            return cleaned, self.dict_mgr.noun_dict[cleaned]

        self.ensure_app_index()
        direct = self._exact_app_index.get(cleaned.lower())
        if direct:
            return direct

        normalized_path = os.path.normcase(cleaned)
        for noun, path in self.dict_mgr.noun_dict.items():
            if isinstance(path, str) and os.path.normcase(path.strip('"')) == normalized_path:
                return noun, path
        return None

    def identify_macro(self, text):
        best_match = None
        best_synonym = ""
        for macro_key, macro_data in self.dict_mgr.macro_dict.items():
            if (
                macro_data.get("type") == "learned"
                and macro_data.get("state", "active") != "active"
            ):
                continue
            for synonym in macro_data["synonyms"]:
                if synonym in text and len(synonym) > len(best_synonym):
                    best_match = macro_key
                    best_synonym = synonym

        if best_match:
            index = text.rfind(best_synonym)
            text_after = text[index + len(best_synonym):].strip()
            if (
                len(text_after) > 5
                or len(text_after.split()) > 1
                or text_after.startswith("서")
                or text_after.startswith("고")
            ):
                return None
            return best_match
        return None

    def identify_app(self, text, normalized_tokens, log_callback, matched_macro=None):
        self.ensure_app_index()
        text_lower = text.lower()
        candidates = self.build_app_candidates(normalized_tokens)

        direct_matches = []
        basename_matches = []
        for candidate in candidates:
            direct = self._exact_app_index.get(candidate)
            if direct:
                noun, path = direct
                position = text_lower.find(noun.lower())
                direct_matches.append((len(noun), -max(position, 0), noun, path))
            basename_match = self._basename_app_index.get(candidate)
            if basename_match:
                noun, path = basename_match
                position = text_lower.find(candidate)
                basename_matches.append(
                    (len(candidate), -max(position, 0), candidate, noun, path)
                )

        if direct_matches:
            _, _, noun, path = max(direct_matches)
            return noun, path
        if basename_matches:
            _, _, candidate, noun, path = max(basename_matches)
            if log_callback:
                log_callback(f"[Parser] 실행 파일명({candidate}) 매칭 적용: '{noun}'")
            return noun, path

        cache_key = tuple(sorted(candidates))
        if matched_macro in {"OPEN", "CLOSE"} and cache_key in self._fuzzy_app_cache:
            return self._fuzzy_app_cache[cache_key]

        for noun_lower, noun, path in self._substring_app_entries:
            if noun_lower and noun_lower in text_lower:
                return noun, path

        for basename, (noun, path) in self._basename_app_index.items():
            if basename and basename in text_lower:
                if log_callback:
                    log_callback(f"[Parser] 실행 파일명({basename}) 매칭 적용: '{noun}'")
                return noun, path

        if matched_macro not in {"OPEN", "CLOSE"}:
            return None, None

        best_by_path = {}
        for candidate in candidates:
            for noun_length, entries in self._fuzzy_app_buckets.items():
                max_length_gap = 1 if noun_length <= 4 else max(2, round(noun_length * 0.25))
                if abs(len(candidate) - noun_length) > max_length_gap:
                    continue
                for noun_clean, noun, path in entries:
                    score = difflib.SequenceMatcher(None, candidate, noun_clean).ratio()
                    current = best_by_path.get(path)
                    if current is None or score > current[0]:
                        best_by_path[path] = (score, noun, candidate)

        if not best_by_path:
            self._fuzzy_app_cache[cache_key] = (None, None)
            return None, None

        ranked = sorted(best_by_path.items(), key=lambda item: item[1][0], reverse=True)
        best_path, (best_score, best_noun, best_candidate) = ranked[0]
        noun_length = len(APP_CLEAN_RE.sub("", best_noun))
        cutoff = 0.85 if noun_length <= 2 else (0.65 if noun_length <= 4 else 0.55)
        second_score = ranked[1][1][0] if len(ranked) > 1 else 0.0

        if best_score >= cutoff and (
            best_score >= 0.90 or best_score - second_score >= 0.08
        ):
            if log_callback:
                log_callback(
                    f"[Parser] 앱 이름 오타 보정: '{best_candidate}' → '{best_noun}' "
                    f"(유사도 {best_score:.2f})"
                )
            result = (best_noun, best_path)
            self._fuzzy_app_cache[cache_key] = result
            return result

        self._fuzzy_app_cache[cache_key] = (None, None)
        return None, None

    def ensure_app_index(self):
        noun_dict = self.dict_mgr.noun_dict
        revision = getattr(self.dict_mgr, "noun_revision", 0)
        signature = (id(noun_dict), len(noun_dict), revision)
        if signature == self._app_index_signature:
            return

        exact_index = {}
        basename_index = {}
        substring_entries = []
        fuzzy_buckets = {}

        for noun, path in noun_dict.items():
            noun_lower = noun.lower()
            noun_clean = APP_CLEAN_RE.sub("", noun_lower)
            if not noun_lower:
                continue

            value = (noun, path)
            exact_index.setdefault(noun_lower, value)
            if noun_clean:
                exact_index.setdefault(noun_clean, value)
            substring_entries.append((noun_lower, noun, path))

            if path and not path.startswith("http"):
                basename = os.path.basename(path).lower().replace(".exe", "").replace(".lnk", "")
                if basename:
                    basename_index.setdefault(APP_CLEAN_RE.sub("", basename), value)

            if path and len(noun_clean) >= 2:
                fuzzy_buckets.setdefault(len(noun_clean), []).append(
                    (noun_clean, noun, path)
                )

        self._exact_app_index = exact_index
        self._basename_app_index = basename_index
        self._substring_app_entries = sorted(
            substring_entries, key=lambda item: len(item[0]), reverse=True
        )
        self._fuzzy_app_buckets = fuzzy_buckets
        self._fuzzy_app_cache = {}
        self._app_index_signature = signature

    @staticmethod
    def build_app_candidates(normalized_tokens):
        candidates = set()
        cleaned_tokens = []
        for token in normalized_tokens:
            clean = APP_CLEAN_RE.sub("", token.lower())
            if len(clean) < 2:
                continue
            candidates.add(clean)
            cleaned_tokens.append(clean)
            for suffix in APP_PARTICLES + APP_FILLER_SUFFIXES:
                if clean.endswith(suffix) and len(clean) - len(suffix) >= 2:
                    candidates.add(clean[:-len(suffix)])

        if len(cleaned_tokens) > 1:
            candidates.add("".join(cleaned_tokens))
            candidates.add(" ".join(cleaned_tokens))
        return candidates

    def looks_like_implicit_search(self, text):
        for engine in self.dict_mgr.search_engines_dict:
            pattern = rf"{re.escape(engine.lower())}에서\s*\S+"
            if re.search(pattern, text.lower()):
                return True
        return False

    def looks_like_local_file_search(self, text):
        lowered = str(text or "").casefold()
        if any(engine.casefold() in lowered for engine in self.dict_mgr.search_engines_dict):
            return False
        has_location = any(
            word in lowered
            for word in (
                "폴더", "디렉터리", "바탕화면", "다운로드", "문서함",
                "드라이브", "경로",
            )
        )
        has_file_target = any(
            word in lowered
            for word in ("파일", "문서", "사진", "동영상", "항목")
        )
        return has_location and has_file_target

    @staticmethod
    def split_compound_command(text):
        separator = "\u241e"
        working = re.sub(r"\s+", " ", text.strip())
        connective_forms = [
            ("실행한 다음에", "실행"), ("실행한 다음", "실행"), ("실행하고", "실행"),
            ("검색한 다음에", "검색"), ("검색한 다음", "검색"), ("검색하고", "검색"),
            ("음소거한 다음에", "음소거"), ("음소거한 다음", "음소거"), ("음소거하고", "음소거"),
            ("재생한 다음에", "재생"), ("재생한 다음", "재생"), ("재생하고", "재생"),
            ("멈춘 다음에", "멈춰"), ("멈춘 다음", "멈춰"), ("멈추고", "멈춰"),
            ("닫은 다음에", "닫아"), ("닫은 다음", "닫아"), ("닫고", "닫아"),
            ("줄인 다음에", "줄여"), ("줄인 다음", "줄여"), ("줄이고", "줄여"),
            ("올린 다음에", "올려"), ("올린 다음", "올려"), ("올리고", "올려"),
            ("찾은 다음에", "찾아"), ("찾은 다음", "찾아"), ("찾고", "찾아"),
            ("켠 다음에", "켜"), ("켠 다음", "켜"), ("켜고", "켜"),
            ("연 다음에", "열어"), ("연 다음", "열어"), ("열고", "열어"),
            ("끈 다음에", "꺼"), ("끈 다음", "꺼"), ("끄고", "꺼"),
        ]

        for connective, imperative in connective_forms:
            working = re.sub(
                rf"{re.escape(connective)}(?=\s)",
                f"{imperative}{separator}",
                working,
            )

        working = re.sub(
            r"\s+(?:그리고|그다음|그 다음|한 다음에|한 다음|그 후에|후에|하고)\s+",
            separator,
            working,
        )
        working = re.sub(r"\s*(?:;|→|\+)\s*", separator, working)
        return [part.strip() for part in working.split(separator) if part.strip()]

    def can_handle_locally(self, text):
        return self.analyze_single_command(text)["executable"]
