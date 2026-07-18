import unittest
from unittest.mock import patch

from engine.parser import CommandParser


class CommandAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = CommandParser()
        cls.parser.dict_mgr.noun_dict = {
            "메모장": "C:\\Windows\\notepad.exe",
            "계산기": "calc.exe",
            "카톡": "C:\\Program Files\\Kakao\\KakaoTalk.exe",
            "파워포인트": "powerpnt.exe",
            "넷플릭스": "https://www.netflix.com",
        }
        cls.parser.dict_mgr.noun_revision += 1
        cls.parser.dict_mgr.search_engines_dict = {
            "네이버": "https://search.naver.com/search.naver?query=",
            "유튜브": "https://www.youtube.com/results?search_query=",
            "구글": "https://www.google.com/search?q=",
            "나무위키": "https://namu.wiki/w/",
            "쿠팡": "https://www.coupang.com/np/search?q=",
        }

    def assert_intent(self, text, macro, app=None, executable=True):
        analysis = self.parser.analyze_command(text)
        self.assertEqual("single", analysis["kind"])
        self.assertEqual(macro, analysis["macro"], text)
        self.assertEqual(app, analysis["app_name"], text)
        self.assertEqual(executable, analysis["executable"], text)
        return analysis

    def test_basic_app_commands(self):
        cases = [
            ("메모장 열어줘", "OPEN", "메모장"),
            ("계산기 켜", "OPEN", "계산기"),
            ("카톡 닫아", "CLOSE", "카톡"),
            ("메모장을열어줘", "OPEN", "메모장"),
            ("계산기가켜줘", "OPEN", "계산기"),
            ("넷플릭스에서열어줘", "OPEN", "넷플릭스"),
        ]
        for text, macro, app in cases:
            with self.subTest(text=text):
                self.assert_intent(text, macro, app)

    def test_fuzzy_app_expectations(self):
        passing = [
            ("메무장 켜", "메모장"),
            ("계산거 열어", "계산기"),
            ("파워포안트 켜", "파워포인트"),
            ("파우포안트 켜", "파워포인트"),
        ]
        for text, app in passing:
            with self.subTest(text=text):
                self.assert_intent(text, "OPEN", app)

        for text in ["카툭 열어", "메무증 켜"]:
            with self.subTest(text=text):
                self.assert_intent(text, "OPEN", None, executable=False)

    def test_search_routing_and_exact_queries(self):
        cases = [
            ("네이버에서 날씨 검색해", "네이버", "날씨", True),
            ("유튜브에 고양이 찾아줘", "유튜브", "고양이", True),
            ("나무위키에서 아이언맨 검색해", "나무위키", "아이언맨", True),
            ("구글에서 파이썬", "구글", "파이썬", True),
            ("이상한사이트에서 날씨 검색해", "구글", "이상한사이트에서 날씨", True),
            ("쿠팡을 검색해", "쿠팡", "", False),
        ]
        for text, engine, query, executable in cases:
            with self.subTest(text=text):
                analysis = self.assert_intent(text, "SEARCH", analysis_app(text), executable)
                self.assertEqual(engine, analysis["search_engine"])
                self.assertEqual(query, analysis["search_query"])

    def test_local_file_search_is_not_misrouted_to_the_web(self):
        for text in (
            "다운로드 폴더에서 최신 파일 찾아줘",
            "사진 폴더에서 큰 파일만 찾아줘",
        ):
            with self.subTest(text=text):
                analysis = self.parser.analyze_command(text)
                self.assertFalse(analysis["executable"])
                self.assertNotEqual("SEARCH", analysis["macro"])

        explicit = self.parser.analyze_command(
            "구글에서 다운로드 파일 찾는 법 검색해줘"
        )
        self.assertTrue(explicit["executable"])
        self.assertEqual("SEARCH", explicit["macro"])

    def test_system_macro_classification(self):
        cases = [
            ("소리 키워", "VOL_UP"),
            ("볼륨 10만큼 올려줘", "VOL_UP"),
            ("볼륨 10만큼 내려줘", "VOL_DOWN"),
            ("소리를 조금만 올려줘", "VOL_UP"),
            ("소리를 살짝 내려줘", "VOL_DOWN"),
            ("볼륨을 30%로 설정해줘", "VOL_SET"),
            ("소리 50으로 맞춰줘", "VOL_SET"),
            ("소리를 30%로 올려줘", "VOL_SET"),
            ("음소거 해줘", "MUTE"),
            ("음소거 해제해줘", "MUTE"),
            ("재생해", "PLAYPAUSE"),
            ("컴퓨터 꺼", "SHUTDOWN"),
            ("취소해", "CANCEL_SHUTDOWN"),
            ("지금 몇 시야?", "TIME"),
            ("오늘 날짜 알려줘", "DATE"),
            ("오늘 날씨 어때?", "WEATHER"),
        ]
        for text, macro in cases:
            with self.subTest(text=text):
                self.assert_intent(text, macro)

    def test_web_close_is_understood_without_executing(self):
        self.assert_intent("넷플릭스 닫아줘", "CLOSE", "넷플릭스")

    def test_compound_analysis_contains_ordered_steps(self):
        analysis = self.parser.analyze_command("메모장 연 다음 계산기 켜줘")
        self.assertEqual("compound", analysis["kind"])
        self.assertTrue(analysis["executable"])
        self.assertEqual(
            [("OPEN", "메모장"), ("OPEN", "계산기")],
            [(step["macro"], step["app_name"]) for step in analysis["steps"]],
        )

    def test_full_learned_template_wins_over_connective_word_split(self):
        parser = CommandParser()
        parser.dict_mgr.noun_dict = {"메모장": "notepad.exe"}
        parser.dict_mgr.macro_dict["메모장_텍스트_입력"] = {
            "name": "메모장_텍스트_입력",
            "type": "learned",
            "app": "메모장",
        }
        parser.dict_mgr.learned_macros = {
            "메모장": {
                "메모장_텍스트_입력": {
                    "state": "active",
                    "code": "",
                    "plan": [{"action": "wait", "seconds": 0.1}],
                    "learning": {
                        "intent": "OPEN_APP_AND_TYPE_TEXT",
                        "utterances": [
                            "{app}을 열고 {text_to_type}라고 입력해줘"
                        ],
                        "slots": [
                            {"name": "app", "type": "app", "required": True},
                            {
                                "name": "text_to_type",
                                "type": "text",
                                "required": True,
                            },
                        ],
                    },
                }
            }
        }
        parser.template_matcher.invalidate()

        analysis = parser.analyze_command(
            "메모장을 열고 JARVIS 5-4 학습 확인이라고 입력해줘"
        )

        self.assertEqual("single", analysis["kind"])
        self.assertEqual("메모장_텍스트_입력", analysis["macro"])
        self.assertEqual("메모장", analysis["app_name"])
        self.assertEqual(
            "jarvis 5-4 학습 확인",
            analysis["template_match"]["slots"]["text_to_type"],
        )

    def test_analysis_never_invokes_external_actions(self):
        with patch("engine.parser.os.startfile") as startfile, \
             patch("engine.parser.subprocess.run") as run, \
             patch("engine.parser.os.system") as system:
            self.parser.analyze_command("컴퓨터 꺼")
            self.parser.analyze_command("카톡 닫아")
            self.parser.analyze_command("메모장 열어줘")

        startfile.assert_not_called()
        run.assert_not_called()
        system.assert_not_called()


def analysis_app(text):
    for name in ("네이버", "유튜브", "나무위키", "구글", "쿠팡"):
        if name in text:
            return name if name in CommandAnalysisTests.parser.dict_mgr.noun_dict else None
    return None


if __name__ == "__main__":
    unittest.main()
