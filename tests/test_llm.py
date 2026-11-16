"""LLM 연동 테스트 (SCORE-02·03, INT-05) — 실제 API를 부르지 않고 가짜 HTTP 함수·가짜 클라이언트로 돌린다."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from config.env import load_env_value, read_env_file
from config.llm import CLASSIFY_RETRIES, PROVIDERS
from mocks.mock_calibration import make_calibration
from mocks.mock_cv_slots import FULL_FOCUS, make_cv_messages
from schemas import SLOT_SECONDS, empty_window_part
from scoring.llm_classify import SYSTEM_PROMPT, build_items, build_user_message, classify_windows, parse_labels
from scoring.llm_client import (
    ChatClient,
    LlmConfigError,
    LlmError,
    _http_json,
    call_with_retries,
    parse_json_object,
)
from scoring.report_text import (
    format_duration,
    generate_report_text,
    report_input,
    template_report_text,
    tidy_text,
)
from scoring.scorer import LlmScorer

T0 = 1_790_000_000.0
CODE = ("VS Code", None, None)
SO = ("Chrome", "stackoverflow.com", None)
GAME = ("Chrome", "youtube.com", "Gaming")
KAKAO = ("KakaoTalk", None, None)


def slot(n: int, window=CODE, *, face=True, drowsy=False, rule=None) -> dict:
    """MergedSlot 하나. window=None이면 창 조회 실패."""
    cv = make_cv_messages(T0, [FULL_FOCUS])[0]["slot"]
    app, domain, category = window if window is not None else (None, None, None)
    return {**cv, "slot": n, "timestamp": T0 + SLOT_SECONDS * n, "face_present": face, "drowsy": drowsy,
            **empty_window_part(), "app_name": app, "domain": domain, "video_category": category,
            "rule_distraction": rule}


class FakeChat:
    """complete_json 응답을 차례로 돌려준다. 예외를 넣으면 그 예외를 던진다."""

    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, float]] = []

    def complete_json(self, system: str, user: str, *, timeout: float) -> dict:
        self.calls.append((system, user, timeout))
        if not self.responses:
            raise LlmError("응답 없음")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def labels_response(*labels: str) -> dict:
    return {"item_labels": [{"id": i, "window_label": label} for i, label in enumerate(labels)]}


# ── 설정·키 ──────────────────────────────────────────

class EnvTest(unittest.TestCase):
    def test_read_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text('﻿# 주석\nGEMINI_API_KEY="g-key"\n\nGROQ_API_KEY = q-key \nEMPTY=\n', encoding="utf-8")
            self.assertEqual(read_env_file(env), {"GEMINI_API_KEY": "g-key", "GROQ_API_KEY": "q-key", "EMPTY": ""})
            self.assertEqual(read_env_file(Path(d) / "없음"), {})

    def test_environment_wins_and_empty_is_none(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=False):
            env = Path(d) / ".env"
            env.write_text("A_KEY=file\nEMPTY=\n", encoding="utf-8")
            os.environ.pop("A_KEY", None)
            self.assertEqual(load_env_value("A_KEY", env), "file")
            self.assertIsNone(load_env_value("EMPTY", env))
            os.environ["A_KEY"] = "env"
            self.assertEqual(load_env_value("A_KEY", env), "env")


# ── 클라이언트 ───────────────────────────────────────

class ParseJsonTest(unittest.TestCase):
    def test_plain_fenced_and_with_prose(self):
        self.assertEqual(parse_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_object('결과입니다: {"a": {"b": 2}} 끝'), {"a": {"b": 2}})

    def test_invalid(self):
        for text in (None, "", "   ", "없음", '{"a": ', "[1, 2]"):
            with self.subTest(text=text), self.assertRaises(LlmError):
                parse_json_object(text)


class ChatClientTest(unittest.TestCase):
    def make(self, response=None, provider="gemini", **kwargs):
        self.requests: list[tuple] = []

        def post(url, headers, body, timeout):
            self.requests.append((url, headers, body, timeout))
            return response if response is not None else {"choices": [{"message": {"content": '{"ok": true}'}}]}

        return ChatClient(PROVIDERS[provider], "secret-key", post_json=post, **kwargs)

    def test_request_shape(self):
        client = self.make()
        self.assertEqual(client.complete_json("시스템", "사용자", timeout=3.0), {"ok": True})
        url, headers, body, timeout = self.requests[0]
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
        self.assertEqual(headers, {"Authorization": "Bearer secret-key"})
        self.assertEqual(body["model"], PROVIDERS["gemini"].model)
        self.assertEqual(body["messages"], [{"role": "system", "content": "시스템"}, {"role": "user", "content": "사용자"}])
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["temperature"], 0.0)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(timeout, 3.0)
        self.assertEqual(client.request_count, 1)

    def test_groq_and_model_override(self):
        client = self.make(provider="groq", model="llama-test")
        client.complete("s", "u", timeout=1)
        url, _, body, _ = self.requests[0]
        self.assertEqual(url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(body["model"], "llama-test")

    def test_content_parts_and_missing_content(self):
        parts = {"choices": [{"message": {"content": [{"type": "text", "text": '{"a"'}, {"text": ": 1}"}]}}]}
        self.assertEqual(self.make(parts).complete_json("s", "u", timeout=1), {"a": 1})
        with self.assertRaises(LlmError):
            self.make({"choices": []}).complete("s", "u", timeout=1)
        with self.assertRaises(LlmError):
            self.make({"choices": [{"message": {"content": None}}]}).complete("s", "u", timeout=1)

    def test_key_not_in_repr(self):
        self.assertNotIn("secret-key", repr(self.make()))

    def test_from_env(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=False):
            for name in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "GROQ_API_KEY"):
                os.environ.pop(name, None)
            env = Path(d) / ".env"
            self.assertIsNone(ChatClient.from_env(env_path=env))  # 파일 없음 → 키 없음
            env.write_text("GEMINI_API_KEY=g\nGROQ_API_KEY=q\n", encoding="utf-8")
            self.assertEqual(ChatClient.from_env(env_path=env).provider.name, "gemini")  # 기본 제공사
            env.write_text("LLM_PROVIDER=Groq\nGROQ_API_KEY=q\nLLM_MODEL=custom\n", encoding="utf-8")
            client = ChatClient.from_env(env_path=env)
            self.assertEqual((client.provider.name, client.model), ("groq", "custom"))
            env.write_text("LLM_PROVIDER=nope\nGEMINI_API_KEY=g\n", encoding="utf-8")
            with self.assertLogs("scoring.llm_client", level="WARNING"):
                self.assertEqual(ChatClient.from_env(env_path=env).provider.name, "gemini")

    def test_list_models(self):
        client = self.make()
        models = client.list_models(get_json=lambda url, headers, timeout: {
            "data": [{"id": "models/gemini-b"}, {"id": "gemini-a"}]})
        self.assertEqual(models, ["gemini-a", "gemini-b"])


class HttpErrorTest(unittest.TestCase):
    def error(self, code: int):
        return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b'{"error": "msg"}'))

    def call(self, side_effect):
        with patch("scoring.llm_client.urllib.request.urlopen", side_effect=side_effect):
            _http_json(object(), 1.0)  # type: ignore[arg-type]

    def test_retryable(self):
        for exc in (self.error(429), self.error(503), urllib.error.URLError("dns"), TimeoutError()):
            with self.subTest(exc=exc), self.assertRaises(LlmError) as ctx:
                self.call(exc)
            self.assertNotIsInstance(ctx.exception, LlmConfigError)

    def test_config_errors(self):
        for code in (400, 401, 403, 404):
            with self.subTest(code=code), self.assertRaises(LlmConfigError) as ctx:
                self.call(self.error(code))
            self.assertIn(str(code), str(ctx.exception))


class RetryTest(unittest.TestCase):
    def test_retries_then_success(self):
        attempts, sleeps = [], []

        def fn():
            attempts.append(1)
            if len(attempts) < 2:
                raise LlmError("일시 오류")
            return "ok"

        self.assertEqual(call_with_retries(fn, retries=1, backoff=0.5, sleep=sleeps.append), "ok")
        self.assertEqual((len(attempts), sleeps), (2, [0.5]))

    def test_gives_up_after_retries(self):
        attempts = []

        def fn():
            attempts.append(1)
            raise LlmError("계속 실패")

        with self.assertRaises(LlmError):
            call_with_retries(fn, retries=2, sleep=lambda s: None)
        self.assertEqual(len(attempts), 3)

    def test_config_error_not_retried(self):
        attempts = []

        def fn():
            attempts.append(1)
            raise LlmConfigError("키 오류")

        with self.assertRaises(LlmConfigError):
            call_with_retries(fn, retries=3, sleep=lambda s: None)
        self.assertEqual(len(attempts), 1)


# ── 창 분류 ──────────────────────────────────────────

class BuildItemsTest(unittest.TestCase):
    def test_unique_sorted_by_count_then_first_seen(self):
        slots = [slot(0, KAKAO), slot(1, CODE), slot(2, SO), slot(3, CODE), slot(4, SO), slot(5, CODE)]
        items = build_items(slots)
        self.assertEqual([(i["id"], i["app_name"], i["domain"], i["slots"]) for i in items],
                         [(0, "VS Code", None, 3), (1, "Chrome", "stackoverflow.com", 2), (2, "KakaoTalk", None, 1)])

    def test_skips_failed_rule_and_self(self):
        slots = [slot(0, None), slot(1, GAME, rule="foreground"), slot(2, CODE, rule="background"),
                 slot(3, ("FocusPomo", None, None)), slot(4, SO)]
        self.assertEqual([i["domain"] for i in build_items(slots)], ["stackoverflow.com"])

    def test_video_category_is_part_of_key(self):
        music = ("Chrome", "youtube.com", "Music")
        edu = ("Chrome", "youtube.com", "Education")
        self.assertEqual(len(build_items([slot(0, music), slot(1, edu)])), 2)

    def test_max_items(self):
        slots = [slot(i, ("App%d" % i, None, None)) for i in range(5)] + [slot(9, ("App4", None, None))]
        items = build_items(slots, max_items=2)
        self.assertEqual([i["app_name"] for i in items], ["App4", "App0"])

    def test_user_message_has_only_window_fields(self):
        message = json.loads(build_user_message(build_items([slot(0, GAME), slot(1, SO)])))
        self.assertEqual(set(message["items"][0]), {"id", "app_name", "domain", "video_category", "slots"})
        self.assertNotIn("face_present", build_user_message(build_items([slot(0, SO)])))


class ParseLabelsTest(unittest.TestCase):
    def setUp(self):
        self.items = build_items([slot(0, CODE), slot(1, SO), slot(2, KAKAO)])

    def test_by_id(self):
        labels = parse_labels(labels_response("work", "work", "unknown"), self.items)
        self.assertEqual(labels, {CODE: "work", SO: "work", KAKAO: "unknown"})

    def test_string_id_missing_and_invalid_label(self):
        data = {"item_labels": [{"id": "0", "window_label": "work"}, {"id": 1, "window_label": "딴짓"},
                                {"id": 99, "window_label": "work"}, "이상한 항목"]}
        labels = parse_labels(data, self.items)
        self.assertEqual(labels, {CODE: "work", SO: "unknown"})  # KAKAO는 빠짐 → 매핑 때 unknown

    def test_falls_back_to_key_without_id(self):
        data = {"item_labels": [{"app_name": "Chrome", "domain": "stackoverflow.com", "video_category": None,
                                 "window_label": "work"}]}
        self.assertEqual(parse_labels(data, self.items), {SO: "work"})

    def test_unusable_response(self):
        for data in ({}, {"item_labels": "work"}, {"item_labels": [{"id": 7, "window_label": "work"}]}):
            with self.subTest(data=data), self.assertRaises(LlmError):
                parse_labels(data, self.items)


class ClassifyTest(unittest.TestCase):
    def setUp(self):
        self.items = build_items([slot(0, CODE), slot(1, GAME), slot(2, KAKAO)])

    def test_success(self):
        chat = FakeChat(labels_response("work", "distraction", "unknown"))
        self.assertEqual(classify_windows(self.items, chat), {CODE: "work", GAME: "distraction", KAKAO: "unknown"})
        system, user, timeout = chat.calls[0]
        self.assertEqual(system, SYSTEM_PROMPT)
        self.assertEqual(json.loads(user)["items"], self.items)

    def test_no_items_no_call(self):
        chat = FakeChat()
        self.assertEqual(classify_windows([], chat), {})
        self.assertEqual(chat.calls, [])

    def test_no_client_is_failure(self):
        self.assertIsNone(classify_windows(self.items, None))

    def test_retry_after_bad_response(self):
        chat = FakeChat({"oops": 1}, labels_response("work", "distraction", "unknown"))
        labels = classify_windows(self.items, chat, sleep=lambda s: None)
        self.assertEqual(labels[GAME], "distraction")
        self.assertEqual(len(chat.calls), 2)

    def test_gives_up_and_returns_none(self):
        chat = FakeChat(*[LlmError("timeout")] * 5)
        with self.assertLogs("scoring.llm_classify", level="WARNING"):
            self.assertIsNone(classify_windows(self.items, chat, sleep=lambda s: None))
        self.assertEqual(len(chat.calls), CLASSIFY_RETRIES + 1)

    def test_config_error_is_not_retried(self):
        chat = FakeChat(LlmConfigError("HTTP 401"), labels_response("work", "work", "work"))
        with self.assertLogs("scoring.llm_classify", level="WARNING"):
            self.assertIsNone(classify_windows(self.items, chat, sleep=lambda s: None))
        self.assertEqual(len(chat.calls), 1)


# ── 리포트 문구 ──────────────────────────────────────

def result_with(*, focus=1050, distraction=0, away=0, drowsy=0, score=78, top=None, slots=150) -> dict:
    """집계값을 직접 정한 SessionResult. slot_scores는 VS Code 작업 슬롯 (창 사용 요약용)."""
    rows = [{"slot": n, "start": T0 + 10 * n, "end": T0 + 10 * n + 10, "app_name": "VS Code", "domain": None,
             "video_category": None, "rule_distraction": None, "window_label": "work", "raw_tag": "focus",
             "final_tag": "focus", "blink_count": 0} for n in range(slots)]
    return {
        "session_start": T0, "focus_score": score, "score_note": None, "score_mode": "full",
        "tag_seconds": {"focus": focus, "distraction": distraction, "away": away, "drowsy": drowsy,
                        "unknown": 0, "no_cam": 0},
        "judged_seconds": focus + distraction + away + drowsy, "longest_focus_seconds": 550,
        "top_distractions": top or [], "slot_scores": rows,
    }


class TemplateTextTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual([format_duration(s) for s in (45, 120, 1050, 0)], ["45초", "2분", "17분 30초", "0초"])

    def test_no_loss(self):
        self.assertIn("이탈 없이 17분 30초", template_report_text(result_with()))

    def test_foreground_distraction_names_site(self):
        top = [{"app_name": "Chrome", "domain": "youtube.com", "video_category": "Gaming", "background": False,
                "seconds": 150}]
        text = template_report_text(result_with(distraction=150, away=90, top=top))
        self.assertIn("가장 큰 이탈은 딴짓(2분 30초)", text)
        self.assertIn("youtube.com [Gaming]", text)

    def test_background_distraction(self):
        top = [{"app_name": None, "domain": None, "video_category": "Gaming", "background": True, "seconds": 200}]
        self.assertIn("틀어 둔 영상", template_report_text(result_with(distraction=200, top=top)))

    def test_drowsy_and_away(self):
        self.assertIn("졸음(1분)", template_report_text(result_with(drowsy=60, away=30)))
        self.assertIn("자리비움(1분 30초)", template_report_text(result_with(away=90)))

    def test_mentions_when_a_window_was_used_long(self):
        """작업이 아닌 창을 1분 이상 이어 쓰면 언제부터 얼마나인지 짚는다 ("이때 유튜브 오래 봤구나")."""
        text = template_report_text(runs_result())
        self.assertIn("02:00부터 3분 20초 동안 youtube.com 창에 머물렀어요.", text)
        self.assertTrue(text.startswith("이탈 없이 2분 40초 집중했어요."))

    def test_no_score_still_mentions_long_run(self):
        result = runs_result()
        result["focus_score"] = None
        text = template_report_text(result)
        self.assertIn("판정할 수 있는 시간이 부족했어요.", text)
        self.assertIn("youtube.com 창에 머물렀어요", text)

    def test_no_score_and_no_slots(self):
        self.assertIn("판정할 수 있는 시간이 부족", template_report_text(result_with(score=None)))
        self.assertIn("기록된 시간이 없어", template_report_text(result_with(slots=0)))


def runs_result() -> dict:
    """VS Code 6분 작업 → 2분 지점부터 youtube.com(미분류) 3분 20초 → 다시 VS Code."""
    def row(n, app, domain, label, tag):
        return {"slot": n, "start": T0 + 10 * n, "end": T0 + 10 * n + 10, "app_name": app, "domain": domain,
                "video_category": None, "rule_distraction": None, "window_label": label, "raw_tag": tag,
                "final_tag": tag, "blink_count": 0}
    rows = ([row(n, "VS Code", None, "work", "focus") for n in range(12)]
            + [row(n, "Chrome", "youtube.com", "unknown", "unknown") for n in range(12, 32)]
            + [row(n, "VS Code", None, "work", "focus") for n in range(32, 36)])
    result = result_with(focus=160, slots=0)
    result.update(slot_scores=rows, session_start=T0)
    return result


class LlmTextTest(unittest.TestCase):
    def test_input_has_only_aggregates(self):
        data = report_input(result_with(distraction=150, top=[
            {"app_name": "Chrome", "domain": "instagram.com", "video_category": None, "background": False,
             "seconds": 150}]))
        self.assertEqual(set(data), {"focus_score", "score_note", "minutes", "longest_focus_minutes",
                                     "top_distractions", "windows", "long_runs"})
        self.assertEqual(data["minutes"]["집중"], 17.5)
        self.assertEqual(data["top_distractions"], [{"name": "instagram.com", "minutes": 2.5}])

    def test_input_has_window_usage_and_long_runs(self):
        data = report_input(runs_result())
        self.assertEqual(data["windows"], [{"name": "youtube.com", "label": "미분류", "minutes": 3.3},
                                           {"name": "VS Code", "label": "작업", "minutes": 2.7}])
        self.assertEqual(data["long_runs"], [{"name": "youtube.com", "label": "미분류", "from": "02:00",
                                              "minutes": 3.3}])
        self.assertNotIn("face", json.dumps(data))

    def test_success_failure_and_empty(self):
        self.assertEqual(generate_report_text(result_with(), FakeChat({"report_text": "  잘했어요.\n 계속해요. "})),
                         "잘했어요. 계속해요.")
        with self.assertLogs("scoring.report_text", level="WARNING"):
            self.assertIsNone(generate_report_text(result_with(), FakeChat(LlmError("timeout"))))
        with self.assertLogs("scoring.report_text", level="WARNING"):
            self.assertIsNone(generate_report_text(result_with(), FakeChat({"report_text": " "})))

    def test_tidy_cuts_at_sentence(self):
        text = "첫 문장이에요. " + "가" * 400
        self.assertEqual(tidy_text(text, max_chars=50), "첫 문장이에요.")
        self.assertTrue(tidy_text("가" * 400, max_chars=10).endswith("…"))


# ── 점수 함수 (분류 → 점수 → 문구) ───────────────────

class LlmScorerTest(unittest.TestCase):
    def run_scorer(self, scorer: LlmScorer, slots: list[dict]) -> dict:
        end = T0 + SLOT_SECONDS * len(slots)
        return scorer(slots, make_calibration(T0 - 5, 1.0), [], session_id="s", session_start=T0, session_end=end)

    def session(self) -> list[dict]:
        # 작업 6슬롯, 유튜브 게임(규칙상 딴짓) 2슬롯, 카톡 1슬롯, 레딧 1슬롯
        reddit = ("Chrome", "reddit.com", None)
        return ([slot(i, CODE) for i in range(6)] + [slot(6, GAME, rule="foreground"), slot(7, GAME, rule="foreground")]
                + [slot(8, KAKAO), slot(9, reddit)])

    def test_full_flow(self):
        chat = FakeChat(labels_response("work", "unknown", "distraction"), {"report_text": "딴짓을 줄여 봐요."})
        result = self.run_scorer(LlmScorer(chat), self.session())
        sent = [(i["app_name"], i["domain"]) for i in json.loads(chat.calls[0][1])["items"]]
        self.assertEqual(sent, [("VS Code", None), ("KakaoTalk", None), ("Chrome", "reddit.com")])  # 규칙 딴짓은 안 보냄
        self.assertFalse(result["llm_call_failed"])
        self.assertEqual(result["score_mode"], "full")
        self.assertEqual([s["final_tag"] for s in result["slot_scores"]],
                         ["focus"] * 6 + ["distraction", "distraction", "unknown", "distraction"])
        self.assertEqual(result["focus_score"], 67)  # 60 / 90초
        self.assertEqual(result["report_text"], "딴짓을 줄여 봐요.")
        self.assertEqual(len(chat.calls), 2)

    def test_classification_failure_falls_back_without_report_call(self):
        chat = FakeChat(*[LlmError("timeout")] * 3)
        with self.assertLogs("scoring.llm_classify", level="WARNING"):
            result = self.run_scorer(LlmScorer(chat, sleep=lambda s: None), self.session())
        self.assertTrue(result["llm_call_failed"])
        self.assertEqual(result["score_mode"], "cam_only")
        self.assertEqual(len(chat.calls), CLASSIFY_RETRIES + 1)  # 문구 호출은 하지 않음
        self.assertEqual(result["report_text"], template_report_text(result))

    def test_report_failure_keeps_classification(self):
        chat = FakeChat(labels_response("work", "unknown", "distraction"), LlmError("timeout"))
        with self.assertLogs("scoring.report_text", level="WARNING"):
            result = self.run_scorer(LlmScorer(chat), self.session())
        self.assertFalse(result["llm_call_failed"])
        self.assertEqual(result["report_text"], template_report_text(result))

    def test_without_client(self):
        result = self.run_scorer(LlmScorer(None), self.session())
        self.assertTrue(result["llm_call_failed"])
        self.assertEqual(result["score_mode"], "cam_only")
        self.assertTrue(result["report_text"])

    def test_no_windows_to_send_counts_as_success(self):
        chat = FakeChat({"report_text": "좋아요."})
        result = self.run_scorer(LlmScorer(chat), [slot(0, GAME, rule="foreground"), slot(1, None)])
        self.assertFalse(result["llm_call_failed"])
        self.assertEqual(len(chat.calls), 1)  # 문구만

    def test_focuspomo_window_is_work_without_asking_llm(self):
        """우리 앱 창은 LLM에 보내지 않고 작업 → 집중 (11-15)."""
        pomo = ("FocusPomo", None, None)
        chat = FakeChat(labels_response("work"), {"report_text": "좋아요."})
        slots = [slot(i, pomo) for i in range(4)] + [slot(i, CODE) for i in range(4, 8)]
        result = self.run_scorer(LlmScorer(chat), slots)
        sent = [i["app_name"] for i in json.loads(chat.calls[0][1])["items"]]
        self.assertEqual(sent, ["VS Code"])
        self.assertEqual({(s["app_name"], s["window_label"], s["final_tag"]) for s in result["slot_scores"]},
                         {("FocusPomo", "work", "focus"), ("VS Code", "work", "focus")})
        self.assertEqual(result["focus_score"], 100)

    def test_only_focuspomo_window_scores_without_call(self):
        chat = FakeChat({"report_text": "좋아요."})
        result = self.run_scorer(LlmScorer(chat), [slot(i, ("FocusPomo", None, None)) for i in range(8)])
        self.assertFalse(result["llm_call_failed"])
        self.assertEqual(result["focus_score"], 100)
        self.assertEqual(len(chat.calls), 1)  # 문구만

    def test_llm_failure_does_not_add_local_labels(self):
        """분류가 실패하면 local_labels를 더하지 않는다 (llm_call_failed 그대로, cam_only에서 어차피 집중)."""
        chat = FakeChat(*[LlmError("timeout")] * 3)
        with self.assertLogs("scoring.llm_classify", level="WARNING"):
            result = self.run_scorer(LlmScorer(chat, sleep=lambda s: None),
                                     [slot(i, ("FocusPomo", None, None)) for i in range(7)] + [slot(7, CODE)])
        self.assertTrue(result["llm_call_failed"])
        self.assertEqual(result["score_mode"], "cam_only")
        self.assertEqual({s["window_label"] for s in result["slot_scores"]}, {"unknown"})
        self.assertEqual({s["final_tag"] for s in result["slot_scores"]}, {"focus"})

    def test_from_env_without_key(self):
        with patch("scoring.scorer.ChatClient.from_env", return_value=None):
            with self.assertLogs("scoring.scorer", level="WARNING"):
                self.assertIsNone(LlmScorer.from_env().client)
        self.assertIsNone(LlmScorer.from_env(enabled=False).client)


if __name__ == "__main__":
    unittest.main()
