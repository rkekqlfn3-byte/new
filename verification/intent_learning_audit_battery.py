# -*- coding: utf-8 -*-
"""집중 감사 배터리: 명령 분해·사전, 학습 자립, 질문 격리, 편집 의도 해석.

실제 앱·AI·사용자 데이터를 건드리지 않는다:
- JARVIS_DATA_DIR 를 임시 폴더로 격리
- 실행 라우트(native/macro/conversation/ai)는 mock 으로 대체해 호출 여부만 기록
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

SCRATCH = Path(__file__).parent
DATA_DIR = Path(tempfile.mkdtemp(prefix="jarvis-audit-data-"))
os.environ["JARVIS_DATA_DIR"] = str(DATA_DIR)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULT = {"A_command": {}, "B_learning": {}, "C_question": {}, "D_edit": {}}


def section_a(parser):
    """명령 분해: 토큰 → 동사(행동 사전) + 명사(noun_dict) 매핑."""
    parser.dict_mgr.noun_dict = {
        "메모장": r"C:\Windows\notepad.exe",
        "계산기": "calc.exe",
        "그림판": "mspaint.exe",
        "카톡": r"C:\Program Files\Kakao\KakaoTalk.exe",
        "엑셀": "excel.exe",
        "넷플릭스": "https://www.netflix.com",
    }
    parser.dict_mgr.noun_revision += 1
    parser.dict_mgr.search_engines_dict = {
        "네이버": "https://search.naver.com/search.naver?query=",
        "구글": "https://www.google.com/search?q=",
        "유튜브": "https://www.youtube.com/results?search_query=",
    }
    cases = [
        # (문장, 기대 macro, 기대 app, 기대 executable)
        ("메모장 열어줘", "OPEN", "메모장", True),
        ("메모장 켜줘", "OPEN", "메모장", True),
        ("메모장 실행해줘", "OPEN", "메모장", True),
        ("메모장 좀 띄워줘", "OPEN", "메모장", True),
        ("계산기 시작해줘", "OPEN", "계산기", True),
        ("계산기를 열어", "OPEN", "계산기", True),
        ("메모장을열어줘", "OPEN", "메모장", True),
        ("넷플릭스 틀어줘", "OPEN", "넷플릭스", True),
        ("카톡 닫아줘", "CLOSE", "카톡", True),
        ("계산기 꺼", "CLOSE", "계산기", True),
        ("메무장 켜", "OPEN", "메모장", True),          # 오타 fuzzy
        ("계산거 열어", "OPEN", "계산기", True),          # 오타 fuzzy
        ("네이버에서 날씨 검색해줘", "SEARCH", None, True),
        ("구글에서 파이썬 문법 찾아줘", "SEARCH", None, True),
        ("유튜브에 고양이 검색해", "SEARCH", None, True),
        ("볼륨 30으로 맞춰줘", "VOL_SET", None, True),
        ("볼륨 조금만 줄여줘", "VOL_DOWN", None, True),
        ("소리 좀 키워줘", "VOL_UP", None, True),
        # 미등록 명사: 동사(OPEN)는 해석되지만 앱을 몰라 AI/재탐색 필요
        ("포토샵 열어줘", "OPEN", None, False),
        ("뿌뿌뿡 열어줘", "OPEN", None, False),
        # 미등록 행동: 로컬 매크로 없음 → AI 경로
        ("사진 정리해줘", None, None, False),
        ("보고서 요약해줘", None, None, False),
    ]
    rows, ok = [], 0
    for text, macro, app, executable in cases:
        analysis = parser.analyze_command(text)
        got = (
            analysis.get("macro"),
            analysis.get("app_name"),
            bool(analysis.get("executable")),
        )
        passed = got == (macro, app, executable)
        ok += passed
        rows.append({
            "text": text,
            "tokens": analysis.get("tokens"),
            "macro": got[0], "app": got[1], "executable": got[2],
            "reason": analysis.get("reason", ""),
            "expected": {"macro": macro, "app": app, "executable": executable},
            "passed": passed,
        })
    RESULT["A_command"] = {"total": len(rows), "passed": ok, "rows": rows}


def section_b(parser):
    """학습(사전 등록) 전후: 같은 문장이 AI 없이 로컬로 해석·라우팅되는가."""
    from engine.pipeline import command_pipeline as cp

    out = {}
    # 1) 학습 전: 미등록 명사 → executable False (AI 필요)
    before = parser.analyze_command("포토샵 열어줘")
    out["before_noun"] = {
        "macro": before.get("macro"), "app": before.get("app_name"),
        "executable": bool(before.get("executable")),
        "reason": before.get("reason"),
    }
    # 2) 명사 사전 등록(AI 성공 후 저장되는 것과 동일한 효과) → executable True
    parser.dict_mgr.noun_dict["포토샵"] = r"C:\Program Files\Adobe\Photoshop.exe"
    parser.dict_mgr.noun_revision += 1
    after = parser.analyze_command("포토샵 열어줘")
    out["after_noun"] = {
        "macro": after.get("macro"), "app": after.get("app_name"),
        "executable": bool(after.get("executable")),
    }
    variant = parser.analyze_command("포토샵 좀 켜줄래")
    out["after_noun_variant"] = {
        "macro": variant.get("macro"), "app": variant.get("app_name"),
        "executable": bool(variant.get("executable")),
    }
    # 3) 학습 매크로 등록 → 동의어 문장이 로컬 인식
    parser.dict_mgr.learned_macros.setdefault("메모장", {})["정리루틴"] = {
        "code": "print('learned')",
        "learning": {"utterances": ["정리 루틴 돌려줘"]},
    }
    parser.dict_mgr.macro_dict["정리루틴"] = {
        "type": "learned", "app": "메모장", "state": "active",
        "synonyms": ["정리 루틴"], "description": "학습 매크로",
    }
    learned = parser.analyze_command("정리 루틴 돌려줘")
    out["learned_macro"] = {
        "macro": learned.get("macro"),
        "executable": bool(learned.get("executable")),
        "reason": learned.get("reason", ""),
    }
    # 4) 라우팅 증명: 인식된 명령은 AI fallback 을 타지 않는다 (실행은 mock)
    calls = {"native": 0, "macro": 0, "ai": 0}

    def fake_native(*a, **k):
        calls["native"] += 1
        return None

    def fake_macro(*a, **k):
        if k.get("matched_macro"):
            calls["macro"] += 1
            return {"success": True, "message": "mock", "action": "macro"}
        return None

    def fake_ai(*a, **k):
        calls["ai"] += 1
        return {"success": False, "message": "mock-ai", "action": "ai"}

    with patch.object(cp, "try_execute_native_route", fake_native), \
         patch.object(cp, "try_execute_macro_route", fake_macro), \
         patch.object(cp, "execute_ai_fallback_route", fake_ai):
        parser.execute_command_result("포토샵 열어줘", mode="command")
        known_calls = dict(calls)
        parser.execute_command_result("완전히 모르는 작업 해줘", mode="command")
        unknown_calls = dict(calls)
    out["routing_known"] = known_calls          # 기대: macro 1, ai 0
    out["routing_unknown"] = {                  # 기대: ai 1 (증가분)
        k: unknown_calls[k] - known_calls[k] for k in calls
    }
    RESULT["B_learning"] = out


def section_c(parser):
    """질문 모드 격리와 로컬 질문 인식."""
    from engine.pipeline import command_pipeline as cp

    out = {}
    date_qs = ["오늘 날짜 알려줘", "오늘 며칠이야?", "오늘이 무슨 요일이지",
               "지금 날짜가 어떻게 돼"]
    time_qs = ["지금 몇 시야", "현재 시간 알려줘", "몇시인지 알려줄래"]
    out["local_date"] = {q: bool(parser._is_current_date_question(q)) for q in date_qs}
    out["local_time"] = {q: bool(parser._is_current_time_question(q)) for q in time_qs}

    calls = {"conversation": 0, "native": 0, "macro": 0, "ai": 0}

    def fake_conv(*a, **k):
        calls["conversation"] += 1
        return {"success": True, "message": "mock-conv", "action": "conversation"}

    def count(name, ret=None):
        def _f(*a, **k):
            calls[name] += 1
            return ret
        return _f

    with patch.object(cp, "execute_conversation_route", fake_conv), \
         patch.object(cp, "try_execute_native_route", count("native")), \
         patch.object(cp, "try_execute_macro_route", count("macro")), \
         patch.object(cp, "execute_ai_fallback_route", count("ai")):
        for q in ["계산기 열어줘", "파일 다 지워줘", "메모장 켜줘",
                  "엑셀에서 뭘 도와줄 수 있어?", "대한민국 수도가 어디야"]:
            parser.execute_command_result(q, mode="question")
    out["question_mode_calls"] = calls  # 기대: conversation 5, 나머지 0
    RESULT["C_question"] = out


def section_d():
    """4개 앱 편집 의도: 구조화 해석 / 안전 안내 / 크래시 분류."""
    from engine.edit_mode.stage5 import Stage5EditError, StructuredEditIntentAnalyzer
    from engine.edit_mode.stage6 import StructuredStage6IntentAnalyzer

    excel_ctx = {
        "app_type": "excel", "selection_kind": "range",
        "selection_reference": "B2:D5", "selected_text_preview": "10, 20, 30",
    }
    excel_cell_ctx = dict(excel_ctx, selection_reference="B2")
    hwp_ctx = {
        "app_type": "hwp", "selection_kind": "text",
        "selected_text_preview": "기존 문장은 여기에 있어요",
        "selected_text_length": 14,
    }
    hwp_reader = lambda: "기존 문장은 여기에 있어요"  # noqa: E731
    word_reader = lambda: "초안 문장입니다 이 문장은 검토가 필요해요"  # noqa: E731
    word_ctx = {
        "app_type": "word", "selection_kind": "text",
        "selected_text_preview": "초안 문장입니다", "selected_text_length": 8,
        "target": {"style_name": "본문", "bold": False, "font_size": 11,
                   "paragraph_alignment": "left"},
    }
    ppt_ctx = {
        "app_type": "powerpoint", "selection_kind": "text",
        "selected_text_preview": "발표 제목", "selected_text_length": 5,
        "target": {"slide_number": 1, "shape_name": "Title 1",
                   "placeholder_type": 13, "left": 10, "top": 10,
                   "width": 100, "height": 40, "font_size": 28,
                   "bold": True, "paragraph_alignment": "center"},
    }
    ppt_shape_ctx = dict(ppt_ctx, selection_kind="shapes")
    batteries = {
        "excel_range": (StructuredEditIntentAnalyzer(), excel_ctx, None, [
            "선택 범위를 굵게 해줘",
            "굵게 해제해줘",
            "글자 크기 14로 바꿔줘",
            "가운데 정렬해줘",
            "'상태' 열을 '완료'로 필터해줘",
            "필터 해제해줘",
            "'매출' 기준 내림차순 정렬해줘",
            "선택 범위에서 '대기'를 '완료'로 바꿔줘",
            "2개 행 추가해줘",
            "1개 열 추가해줘",
            "현재 선택 내용 읽어줘",
        ]),
        "excel_cell": (StructuredEditIntentAnalyzer(), excel_cell_ctx, None, [
            "'검토 완료' 입력해줘",
            "42 입력해줘",
            "'=SUM(B2:B6)' 입력해줘",
            "셀 병합해줘",                      # 미지원 → 안내 기대
            "차트 만들어줘",                    # 미지원 → 안내 기대
            "알아서 예쁘게 해줘",               # 모호 → 안내 기대
        ]),
        "hwp": (StructuredEditIntentAnalyzer(), hwp_ctx, hwp_reader, [
            "선택 문장을 '한글 교체 시험 문장입니다.'로 바꿔줘",
            "'임시'를 '확정'으로 바꿔줘",
            "문서 전체에서 '테스트'를 '시험'으로 바꿔줘",
            "선택 글자를 굵게 해줘",
            "굵게 해제해줘",
            "글자 크기 12로 바꿔줘",
            "조금 크게 해줘",
            "현재 문단을 가운데 정렬해줘",
            "양쪽 정렬해줘",
            "조금 줄여줘",
            "보고서체로 바꿔줘",
            "글자 크게 해줘",
            "글자 크기 조금 크게 해줘",
            "현재 선택 내용 읽어줘",
            "표 만들어줘",                      # 미지원 → 안내 기대
            "그림 넣어줘",                      # 미지원 → 안내 기대
        ]),
        "word": (StructuredStage6IntentAnalyzer(), word_ctx, word_reader, [
            "이 문장을 'Word 교체 시험 문장입니다.'로 바꿔줘",
            "조금 줄여줘",
            "격식체로 바꿔줘",
            "굵게 해줘",
            "굵게 해제해줘",
            "글자 크기 14로 바꿔줘",
            "조금 크게 해줘",
            "조금 작게 해줘",
            "가운데 정렬해줘",
            "양쪽 정렬해줘",
            "현재 선택의 스타일과 서식 알려줘",
            "현재 Word 문서 저장해줘",
            "머리말 넣어줘",                    # 미지원 → 안내 기대
            "쪽 번호 넣어줘",                   # 미지원 → 안내 기대
        ]),
        "powerpoint_text": (StructuredStage6IntentAnalyzer(), ppt_ctx, None, [
            "선택한 제목을 'PPT 시험 제목'으로 바꿔줘",
            "굵게 해줘",
            "글자 크기 28로 바꿔줘",
            "조금 크게 해줘",
            "가운데 정렬해줘",
            "앞 슬라이드와 같은 스타일로 맞춰줘",
            "현재 도형 위치와 크기 알려줘",
            "새 슬라이드 추가해줘",             # 미지원 → 안내 기대
            "애니메이션 넣어줘",                # 미지원 → 안내 기대
        ]),
        "powerpoint_shape": (StructuredStage6IntentAnalyzer(), ppt_shape_ctx, None, [
            "오른쪽으로 20pt 이동해줘",
            "위로 10pt 올려줘",
            "이 도형 크게 해줘",
            "이 도형 작게 해줘",
        ]),
    }
    summary = {}
    for app, (analyzer, ctx, reader, sentences) in batteries.items():
        rows = []
        counts = {"intent": 0, "guidance": 0, "crash": 0}
        for sentence in sentences:
            try:
                intent = analyzer.analyze(sentence, ctx, reader)
                counts["intent"] += 1
                rows.append({"text": sentence, "outcome": "intent",
                             "operation": intent.operation,
                             "description": intent.description})
            except Stage5EditError as error:
                counts["guidance"] += 1
                rows.append({"text": sentence, "outcome": "guidance",
                             "message": str(error)})
            except Exception as error:  # noqa: BLE001 - 감사 분류용
                counts["crash"] += 1
                rows.append({"text": sentence, "outcome": "crash",
                             "error_type": type(error).__name__,
                             "message": str(error)})
        summary[app] = {"counts": counts, "rows": rows}
    RESULT["D_edit"] = summary


def main():
    from engine.parser import CommandParser

    parser = CommandParser()
    section_a(parser)
    section_b(parser)
    section_c(parser)
    section_d()
    out_path = SCRATCH / "intent_learning_audit_report.json"
    out_path.write_text(
        json.dumps(RESULT, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    a = RESULT["A_command"]
    print(f"A 명령 분해: {a['passed']}/{a['total']}")
    print("B 학습 라우팅:", json.dumps({
        k: v for k, v in RESULT["B_learning"].items()
        if k.startswith(("routing", "learned", "after_noun", "before_noun"))
    }, ensure_ascii=False))
    print("C 질문 격리:", json.dumps(RESULT["C_question"]["question_mode_calls"],
                                    ensure_ascii=False))
    for app, data in RESULT["D_edit"].items():
        print(f"D {app}:", json.dumps(data["counts"], ensure_ascii=False))
    print("결과 파일:", out_path)


if __name__ == "__main__":
    main()
