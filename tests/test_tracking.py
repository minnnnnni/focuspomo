"""추적 모드 테스트 (core/tracking.py): 웹캠 + 창 / 웹캠만 / 창만 / 타이머만.

모드 값·저장, 점수 모드 강제, LLM 호출 여부, 세션 워커 건너뛰기, 대기 화면 선택 칸, 세션 흐름(캘리브레이션 생략),
타이머만 리포트, 카메라 스레드 켜고 끄기 (offscreen).
"""
import json
import os
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main  # noqa: E402
from config.aggregation import CALIBRATION_SEC  # noqa: E402
from core.session_workers import SessionWorkers  # noqa: E402
from core.tracking import (  # noqa: E402
    DEFAULT_TRACKING_MODE,
    TRACKING_MODE_HINT_KO,
    TrackingMode,
    no_camera_calibration,
)
from core.history import summarize  # noqa: E402
from gui.history_view import NOT_SCORED_TIP, score_tooltip  # noqa: E402
from gui.idle_view import CALIBRATION_HINT, CAMERA_OFF_TEXT, CAMERA_STATUS_TEXT, IdleView  # noqa: E402
from gui.main_window import TRACKING_SAVE_FAILED_NOTICE, MainWindow, Screen  # noqa: E402
from gui.report_view import TIMER_ONLY_CAPTION  # noqa: E402
from mocks.mock_calibration import MockCalibrator, make_calibration  # noqa: E402
from mocks.mock_camera import InstantMockCamera, MockCameraThread  # noqa: E402
from mocks.mock_cv_slots import FULL_FOCUS, make_cv_messages  # noqa: E402
from mocks.mock_scoring import MockScorer  # noqa: E402
from mocks.mock_window_slots import APP_VSCODE, make_window_messages  # noqa: E402
from schemas import SLOT_SECONDS, empty_cv_part, empty_window_part  # noqa: E402
from scoring.focus_score import (  # noqa: E402
    CAM_ONLY_CHOSEN_NOTE,
    INSUFFICIENT_NOTE,
    SCORE_NOTES,
    TIMER_ONLY_NOTE,
    choose_mode,
    score_session,
)
from scoring.scorer import LlmScorer  # noqa: E402
from storage import session_store, settings_store  # noqa: E402
from core.pomodoro import PomodoroSettings  # noqa: E402

app = QApplication.instance() or QApplication([])

START = 1_790_753_400.0
CODE = ("Code.exe", None, None)


def slot(n: int, window=CODE, face=True, **fields) -> dict:
    row = {"slot": n, "timestamp": START + n * SLOT_SECONDS, **empty_cv_part(), **empty_window_part()}
    row["face_present"] = face
    if window is not None:
        row["app_name"], row["domain"], row["video_category"] = window
    row.update(fields)
    return row


def run(slots, labels, **flags):
    end = START + len(slots) * SLOT_SECONDS
    return score_session(slots, make_calibration(START - 5.0, face_detected_ratio=1.0), [],
                         session_id="s", session_start=START, session_end=end,
                         llm_labels=labels, report_text="문구", **flags)


class TrackingModeTest(unittest.TestCase):
    def test_flags(self):
        self.assertEqual({m: (m.uses_camera, m.uses_window) for m in TrackingMode}, {
            TrackingMode.BOTH: (True, True),
            TrackingMode.CAM_ONLY: (True, False),
            TrackingMode.WINDOW_ONLY: (False, True),
            TrackingMode.TIMER_ONLY: (False, False),
        })

    def test_parse_unknown_is_default(self):
        self.assertIs(TrackingMode.parse("timer_only"), TrackingMode.TIMER_ONLY)
        for bad in (None, "", "camera", 3):
            self.assertIs(TrackingMode.parse(bad), DEFAULT_TRACKING_MODE)

    def test_no_camera_calibration(self):
        c = no_camera_calibration(123.0)
        self.assertEqual((c["ear_baseline"], c["valid_frames"], c["ended_at"], c["fallback_used"]), (0.0, 0, 123.0, False))


class TrackingStoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"

    def test_default_and_round_trip(self):
        self.assertIs(settings_store.load_tracking_mode(self.data_dir), TrackingMode.BOTH)
        settings_store.save_tracking_mode(TrackingMode.WINDOW_ONLY, self.data_dir)
        self.assertIs(settings_store.load_tracking_mode(self.data_dir), TrackingMode.WINDOW_ONLY)

    def test_keys_kept_by_each_other(self):
        settings_store.save_tracking_mode(TrackingMode.CAM_ONLY, self.data_dir)
        settings_store.save_settings(PomodoroSettings(focus_min=50), self.data_dir)
        self.assertIs(settings_store.load_tracking_mode(self.data_dir), TrackingMode.CAM_ONLY)
        settings_store.save_tracking_mode(TrackingMode.TIMER_ONLY, self.data_dir)
        self.assertEqual(settings_store.load_settings(self.data_dir).focus_min, 50)

    def test_unknown_value_is_default(self):
        self.data_dir.mkdir(parents=True)
        settings_store.settings_path(self.data_dir).write_text(json.dumps({"tracking_mode": "x"}), encoding="utf-8")
        self.assertIs(settings_store.load_tracking_mode(self.data_dir), TrackingMode.BOTH)

    def test_delete_records_keeps_tracking_mode(self):
        settings_store.save_tracking_mode(TrackingMode.TIMER_ONLY, self.data_dir)
        session_store.delete_all_records(self.data_dir)
        self.assertIs(settings_store.load_tracking_mode(self.data_dir), TrackingMode.TIMER_ONLY)


class ScoreModeTest(unittest.TestCase):
    """사용자가 끈 쪽은 데이터와 상관없이 점수에 쓰지 않는다."""

    def test_choose_mode_flags(self):
        slots, d = [slot(i) for i in range(10)], [10.0] * 10
        self.assertEqual(choose_mode(slots, d, True), "full")
        self.assertEqual(choose_mode(slots, d, True, use_window=False), "cam_only")
        self.assertEqual(choose_mode(slots, d, True, use_camera=False), "window_only")
        self.assertEqual(choose_mode(slots, d, True, use_camera=False, use_window=False), "none")

    def test_cam_only_chosen(self):
        # 창 추적을 껐으니 창 필드는 None. 얼굴 있으면 집중, 없으면 자리비움
        slots = [slot(i, None) for i in range(8)] + [slot(i, None, face=False) for i in range(8, 10)]
        r = run(slots, {}, use_window=False)
        self.assertEqual((r["score_mode"], r["focus_score"]), ("cam_only", 80))
        self.assertEqual(r["score_note"], CAM_ONLY_CHOSEN_NOTE)
        self.assertFalse(r["llm_call_failed"])  # LLM을 부르지 않았다

    def test_cam_only_chosen_without_camera_data_is_none(self):
        r = run([slot(i, None, face=None) for i in range(10)], {}, use_window=False)
        self.assertEqual((r["score_mode"], r["focus_score"], r["score_note"]), ("none", None, SCORE_NOTES["none"]))

    def test_window_only_chosen(self):
        r = run([slot(i, face=None) for i in range(10)], {CODE: "work"}, use_camera=False)
        self.assertEqual((r["score_mode"], r["focus_score"], r["score_note"]),
                         ("window_only", 100, SCORE_NOTES["window_only"]))

    def test_window_only_chosen_insufficient(self):
        r = run([slot(i, None, face=None) for i in range(10)], {}, use_camera=False)
        self.assertEqual((r["focus_score"], r["score_note"]), (None, INSUFFICIENT_NOTE))

    def test_timer_only(self):
        r = run([slot(i, None, face=None) for i in range(10)], {}, use_camera=False, use_window=False)
        self.assertEqual((r["score_mode"], r["focus_score"], r["score_note"]), ("none", None, TIMER_ONLY_NOTE))
        self.assertFalse(r["llm_call_failed"])
        self.assertEqual(r["judged_seconds"], 0)


class FakeChat:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user, *, timeout):
        self.calls.append(user)
        return self.responses.pop(0)


class ScorerTrackingTest(unittest.TestCase):
    def call(self, scorer, slots, **flags):
        end = START + len(slots) * SLOT_SECONDS
        return scorer(slots, make_calibration(START - 5, 1.0), [], session_id="s", session_start=START,
                      session_end=end, **flags)

    def test_cam_only_skips_classification(self):
        chat = FakeChat({"report_text": "좋아요."})
        result = self.call(LlmScorer(chat), [slot(i, None) for i in range(10)], use_window=False)
        self.assertEqual(len(chat.calls), 1)  # 문구만 (창 분류 호출 없음)
        self.assertNotIn("items", json.loads(chat.calls[0]))
        self.assertEqual((result["score_mode"], result["report_text"]), ("cam_only", "좋아요."))

    def test_timer_only_calls_nothing(self):
        chat = FakeChat()
        result = self.call(LlmScorer(chat), [slot(i, None, face=None) for i in range(10)],
                           use_camera=False, use_window=False)
        self.assertEqual(chat.calls, [])
        self.assertEqual((result["score_note"], result["report_text"]), (TIMER_ONLY_NOTE, ""))

    def test_mock_scorer_matches(self):
        scorer = MockScorer()
        r = self.call(scorer, [slot(i, None, face=None) for i in range(10)], use_camera=False, use_window=False)
        self.assertEqual((r["score_note"], r["report_text"]), (TIMER_ONLY_NOTE, ""))
        self.assertEqual(scorer.tracking_calls, [(False, False)])
        r = self.call(MockScorer(llm_fail=True), [slot(i, None) for i in range(10)], use_window=False)
        self.assertFalse(r["llm_call_failed"])


class StartedWorker(threading.Thread):
    def __init__(self, *_):
        super().__init__(daemon=True)
        self.stopped = threading.Event()

    def stop(self):
        self.stopped.set()

    def run(self):
        self.stopped.wait()


class WorkerGateTest(unittest.TestCase):
    def test_none_factory_is_skipped(self):
        workers = SessionWorkers(queue.Queue(), [lambda q, s: None, StartedWorker])
        workers.start(1.0)
        self.assertEqual(workers.running_count, 1)
        workers.shutdown()

    def test_mode_gated(self):
        enabled = [False]
        gated = main.mode_gated(StartedWorker, lambda: enabled[0])
        self.assertIsNone(gated(queue.Queue(), 1.0))
        enabled[0] = True
        self.assertIsInstance(gated(queue.Queue(), 1.0), StartedWorker)


class IdleViewModeTest(unittest.TestCase):
    def setUp(self):
        self.view = IdleView()
        self.addCleanup(self.view.deleteLater)

    def test_camera_mode_needs_camera(self):
        self.view.set_camera_ready(None)
        self.assertFalse(self.view.start_button.isEnabled())
        self.assertEqual(self.view.hint_label.text(), CALIBRATION_HINT)
        self.view.set_camera_ready(True)
        self.assertTrue(self.view.start_button.isEnabled())

    def test_no_camera_mode_starts_without_camera(self):
        self.view.set_camera_ready(False)
        self.view.set_tracking_mode(TrackingMode.WINDOW_ONLY)
        self.assertTrue(self.view.start_button.isEnabled())
        self.assertEqual(self.view.camera_label.text(), CAMERA_OFF_TEXT)
        self.assertEqual(self.view.hint_label.text(), TRACKING_MODE_HINT_KO[TrackingMode.WINDOW_ONLY])
        self.assertTrue(self.view.mode_buttons[TrackingMode.WINDOW_ONLY].isChecked())
        self.view.set_tracking_mode(TrackingMode.CAM_ONLY)
        self.assertFalse(self.view.start_button.isEnabled())  # 웹캠이 쓸 수 없는 상태 그대로
        self.assertNotEqual(self.view.camera_label.text(), CAMERA_OFF_TEXT)

    def test_click_requests_without_applying(self):
        got = []
        self.view.tracking_mode_requested.connect(got.append)
        self.view.mode_buttons[TrackingMode.TIMER_ONLY].click()
        self.assertEqual(got, ["timer_only"])
        self.assertIs(self.view.tracking_mode, TrackingMode.BOTH)  # 적용은 MainWindow가
        self.assertTrue(self.view.mode_buttons[TrackingMode.BOTH].isChecked())
        self.view.mode_buttons[TrackingMode.BOTH].click()
        self.assertEqual(got, ["timer_only"])  # 같은 모드는 다시 보내지 않는다


class WindowModeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        p = patch("gui.main_window.FLUSH_WAIT_MS", 0)
        p.start()
        self.addCleanup(p.stop)

    def make_window(self, mode=None) -> MainWindow:
        self.queue = queue.Queue()
        self.scorer = MockScorer()
        self.camera = InstantMockCamera(MockCalibrator("success"))
        w = MainWindow(camera=self.camera, load_previous_baseline=lambda: None, slot_queue=self.queue,
                       score_session=self.scorer, data_dir=self.data_dir, tracking_mode=mode)
        self.addCleanup(w.deleteLater)
        return w

    def saved(self) -> list[dict]:
        path = session_store.sessions_path(self.data_dir)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_loads_saved_mode(self):
        settings_store.save_tracking_mode(TrackingMode.CAM_ONLY, self.data_dir)
        w = self.make_window()
        self.assertIs(w.tracking_mode, TrackingMode.CAM_ONLY)
        self.assertIs(w.idle_view.tracking_mode, TrackingMode.CAM_ONLY)

    def test_selector_saves_and_emits(self):
        w = self.make_window()
        got = []
        w.tracking_mode_changed.connect(got.append)
        w.idle_view.mode_buttons[TrackingMode.WINDOW_ONLY].click()
        self.assertIs(w.tracking_mode, TrackingMode.WINDOW_ONLY)
        self.assertEqual(got, ["window_only"])
        self.assertIs(settings_store.load_tracking_mode(self.data_dir), TrackingMode.WINDOW_ONLY)

    def test_save_failure_still_applies(self):
        w = self.make_window()
        with patch.object(settings_store, "save_tracking_mode", side_effect=OSError("denied")), \
                self.assertLogs("gui.main_window", level="WARNING"):
            self.assertFalse(w.set_tracking_mode("timer_only"))
        self.assertIs(w.tracking_mode, TrackingMode.TIMER_ONLY)
        self.assertEqual(w.idle_view.notice_label.text(), TRACKING_SAVE_FAILED_NOTICE)

    def test_ignored_outside_idle(self):
        w = self.make_window()
        w.go_settings()
        w.set_tracking_mode("timer_only")
        self.assertIs(w.tracking_mode, TrackingMode.BOTH)

    def test_timer_only_session_skips_calibration_and_saves(self):
        w = self.make_window(TrackingMode.TIMER_ONLY)
        w.set_camera(None)  # 앱은 웹캠을 끈다
        starts = []
        w.session_started.connect(starts.append)
        with patch("gui.main_window.time.time", return_value=START):
            w.idle_view.start_button.click()
        self.assertEqual(w.screen, Screen.TIMER)
        self.assertEqual((w.session_start, starts), (START, [START]))
        self.assertEqual(w.calibration["ear_baseline"], 0.0)
        w.end_session(START + 120)
        self.assertEqual(w.screen, Screen.REPORT)
        result = w.session_result
        self.assertEqual((result["score_note"], result["focus_score"]), (TIMER_ONLY_NOTE, None))
        self.assertEqual(self.scorer.tracking_calls, [(False, False)])
        self.assertEqual(self.saved(), [result])
        # 리포트: 점수 자리에 집중한 시간, 통계·표는 숨김
        view = w.report_view
        self.assertEqual(view.score_caption.text(), TIMER_ONLY_CAPTION)
        self.assertEqual(view.score_label.text(), "02:00")
        self.assertTrue(view.stats_widget.isHidden())
        self.assertTrue(view.table_card.isHidden())
        self.assertTrue(view.timeline_card.isHidden())
        # 0.0 기준값은 다음 세션의 직전 기준값으로 쓰지 않는다
        self.assertIsNone(session_store.load_previous_baseline(self.data_dir))
        # 기록 화면: 점수 없음 이유
        self.assertEqual(score_tooltip(summarize(result)), NOT_SCORED_TIP)

    def test_window_only_session_uses_window_slots(self):
        w = self.make_window(TrackingMode.WINDOW_ONLY)
        with patch("gui.main_window.time.time", return_value=START):
            w.begin_session()
        self.assertEqual(w.screen, Screen.TIMER)  # 캘리브레이션 없이
        for msg in make_window_messages(START, [APP_VSCODE] * 12):
            self.queue.put(msg)
        w.end_session(START + 12 * SLOT_SECONDS)
        result = w.session_result
        self.assertEqual(self.scorer.tracking_calls, [(False, True)])
        self.assertEqual(result["score_mode"], "window_only")
        self.assertFalse(w.report_view.stats_widget.isHidden())
        # 다음 세션도 캘리브레이션 없이
        w.report_view.next_button.click()
        self.assertEqual(w.screen, Screen.TIMER)

    def test_cam_only_session_calibrates(self):
        w = self.make_window(TrackingMode.CAM_ONLY)
        w.poll_camera_events()
        w.idle_view.set_camera_ready(True)
        w.begin_session()
        self.assertEqual(w.screen, Screen.CALIBRATION)
        for _ in range(CALIBRATION_SEC):
            w.calibration_view.tick()
        self.assertEqual(w.screen, Screen.TIMER)
        start = w.session_start
        for msg in make_cv_messages(start, [FULL_FOCUS] * 12):
            self.queue.put(msg)
        w.end_session(start + 12 * SLOT_SECONDS)
        result = w.session_result
        self.assertEqual(self.scorer.tracking_calls, [(True, False)])
        self.assertEqual((result["score_mode"], result["focus_score"], result["score_note"]),
                         ("cam_only", 100, CAM_ONLY_CHOSEN_NOTE))

    def test_mode_is_fixed_for_running_session(self):
        w = self.make_window(TrackingMode.TIMER_ONLY)
        w.begin_session()
        w.tracking_mode = TrackingMode.BOTH  # 세션 중에는 선택 칸이 없지만, 바뀌어도 세션 모드는 그대로
        w.end_session(w.session_start + 60)
        self.assertEqual(self.scorer.tracking_calls, [(False, False)])

    def test_no_camera_link_is_ignored(self):
        w = self.make_window()
        w.set_camera(None)
        w.poll_camera_events()  # 오류 없이 넘어간다
        self.assertIsNone(w.idle_view.camera_ready)
        self.assertEqual(w.idle_view.camera_label.text(), CAMERA_STATUS_TEXT[None])


def wait_until(condition, timeout_ms: int = 3000) -> None:
    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    assert condition(), "조건이 시간 안에 참이 되지 않았습니다"


class AppCameraTest(unittest.TestCase):
    """웹캠을 쓰지 않는 모드면 카메라 스레드를 멈추고, 돌아오면 새 스레드를 띄운다."""

    def build(self, mode: TrackingMode):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings_store.save_tracking_mode(mode, Path(tmp.name))
        self.threads = []

        def camera_factory(link):
            thread = MockCameraThread(link, measure_sec=0.01)
            self.threads.append(thread)
            return thread

        built = main.build_app(Path(tmp.name), factories=[], camera_thread_factory=camera_factory, scorer=MockScorer())
        self.addCleanup(built.window.deleteLater)
        self.addCleanup(built.shutdown)
        built.start()
        return built

    def test_starts_without_camera_in_timer_only(self):
        built = self.build(TrackingMode.TIMER_ONLY)
        self.assertFalse(built.camera_running)
        self.assertIsNone(built.window.camera)
        self.assertFalse(self.threads[0].is_alive())
        self.assertTrue(built.window.idle_view.start_button.isEnabled())

    def test_switching_stops_and_restarts_camera(self):
        built = self.build(TrackingMode.BOTH)
        wait_until(lambda: built.window.idle_view.camera_ready is True)
        first = built.camera_thread
        built.window.set_tracking_mode("window_only")
        first.join(3)
        self.assertFalse(first.is_alive())  # 웹캠을 놓았다
        self.assertIsNone(built.window.camera)
        built.window.set_tracking_mode("cam_only")
        self.assertIsNot(built.camera_thread, first)
        self.assertIs(built.window.camera, built.camera)
        wait_until(lambda: built.window.idle_view.camera_ready is True)
        built.window.set_tracking_mode("both")  # 웹캠 모드끼리는 그대로
        self.assertEqual(len(self.threads), 2)

    def test_default_factories_follow_session_mode(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        built = main.build_app(Path(tmp.name), camera_thread_factory=lambda link: MockCameraThread(link),
                               scorer=MockScorer())
        self.addCleanup(built.window.deleteLater)
        factories = built.workers._factories
        built.window.session_tracking = TrackingMode.TIMER_ONLY
        self.assertEqual([f(queue.Queue(), 1.0) for f in factories], [None, None])
        built.window.session_tracking = TrackingMode.CAM_ONLY
        built.window.calibration = make_calibration(0.0, face_detected_ratio=1.0)
        cv, window = [f(queue.Queue(), 1.0) for f in factories]
        self.assertIsNotNone(cv)
        self.assertIsNone(window)


if __name__ == "__main__":
    unittest.main()
