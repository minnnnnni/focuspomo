"""FocusPomo 앱 진입점 (INT-01): 큐를 만들고 GUI, 카메라 스레드, 세션 워커를 연결해 실행한다.

세션이 시작되면(session_started) 세션 워커를 session_start와 함께 만들어 시작하고,
세션이 끝나면(session_ending) 종료 신호를 보낸다. 앱을 끌 때 끝나기를 기다린다.
카메라 스레드는 앱 수명 동안 돌고, GUI와 명령·이벤트 큐로만 주고받는다 (core.camera_link).

추적 모드 (core.tracking, 대기 화면에서 고름): 웹캠을 쓰지 않는 모드(창만·타이머만)에서는 카메라 스레드를 멈춰
웹캠을 놓고, 창 추적을 쓰지 않는 모드(웹캠만·타이머만)에서는 창 스레드를 만들지 않는다 (App, mode_gated).

실제로 연결된 것: 웹캠 CV (cv_module.worker, CV-05), 활성 창 추적, 점수 (scoring.scorer.LlmScorer, INT-05).
점수 계산은 LLM을 부르므로 GUI 밖 스레드에서 돈다 (MainWindow score_in_background).
.env에 LLM 키(GEMINI_API_KEY 등)가 없거나 --no-llm이면 창 분류 없이 계산한다 (cam_only 모드, 템플릿 문구).

유튜브 카테고리는 기본으로 켠다 (딴짓 카테고리 규칙에 필요, 점수 명세 2.1). 영상 ID가 YouTube Data API로 나가며
--no-youtube로 끈다. .env에 YOUTUBE_API_KEY가 없으면 카테고리 없이(None) 동작한다.

    python main.py
    python main.py --no-youtube              # 유튜브 카테고리 조회 끄기 (영상 ID를 외부로 보내지 않음)
    python main.py --no-llm                  # LLM 창 분류·문구 끄기 (창 목록을 외부로 보내지 않음)
    python main.py --data-dir C:\\temp\\fp-data  # 개발 중 기록을 다른 폴더에 저장
    python main.py --camera 1                # 다른 웹캠 번호
    python main.py --mock-camera             # 웹캠 없이 mock 카메라 (아래 옵션은 mock에서만)
    python main.py --mock-camera --scenario fail_twice     # mock 캘리브레이션 시나리오
    python main.py --mock-camera --no-camera               # mock 웹캠을 열지 못함: 시작 버튼 꺼짐
    python main.py --mock-camera --camera-lost-after 8     # mock 웹캠이 8초 뒤 끊겼다가 5초 뒤 복구
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.camera_link import CameraLink
from core.session_end import ScoreFn
from core.session_workers import SessionWorkers, WorkerFactory
from core.tracking import TrackingMode
from gui.main_window import MainWindow
from gui.style import load_fonts
from mocks.mock_calibration import SCENARIOS as CALIBRATION_SCENARIOS
from cv_module.worker import CameraThread, CvSessionWorker
from mocks.mock_camera import MockCameraThread, MockCvSessionWorker
from scoring.scorer import LlmScorer
from window_tracker.tracker import WindowTracker
from window_tracker.youtube_probe import YouTubeCategoryLookup

CAMERA_JOIN_TIMEOUT_SEC = 2.0  # 앱을 끌 때 카메라 스레드가 웹캠을 놓기를 기다리는 시간


CameraThreadFactory = Callable[[CameraLink], "CameraThread | MockCameraThread"]


class App:
    """창·세션 워커·카메라 스레드. 카메라 스레드는 웹캠을 쓰는 추적 모드일 때만 돈다 (core.tracking).

    웹캠을 쓰지 않는 모드로 바꾸면 stop 명령을 보내 웹캠을 놓게 하고, 다시 웹캠 모드로 오면
    새 CameraLink와 새 카메라 스레드를 만든다 (스레드는 다시 시작할 수 없고, 멈추는 중인 이전 스레드가
    새 명령을 가져가지 않도록 큐도 새로 만든다). 모델 로딩·첫 프레임까지 몇 초 동안은 "웹캠 확인 전"이다.
    """

    def __init__(self, window: MainWindow, workers: SessionWorkers, camera: CameraLink,
                 camera_thread: CameraThread | MockCameraThread, camera_thread_factory: CameraThreadFactory) -> None:
        self.window = window
        self.workers = workers
        self.camera = camera
        self.camera_thread = camera_thread
        self._camera_thread_factory = camera_thread_factory
        self._camera_on = False
        self._stopped_threads: list[CameraThread | MockCameraThread] = []  # stop을 보낸 이전 카메라 스레드
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.window.tracking_mode_changed.connect(self.apply_tracking_mode)
        self.apply_tracking_mode(self.window.tracking_mode)

    def apply_tracking_mode(self, mode: str) -> None:
        """추적 모드에 맞게 카메라 스레드를 켜거나 끈다."""
        if TrackingMode(mode).uses_camera:
            self._start_camera()
        else:
            self._stop_camera()

    @property
    def camera_running(self) -> bool:
        return self._camera_on

    def _start_camera(self) -> None:
        if self._camera_on:
            return
        if self.camera_thread.ident is not None:  # 이미 한 번 돌았던 스레드: 새로 만든다
            self.camera = CameraLink()
            self.camera_thread = self._camera_thread_factory(self.camera)
        self.window.set_camera(self.camera)
        self.camera_thread.start()
        self._camera_on = True

    def _stop_camera(self) -> None:
        if not self._camera_on:
            self.window.set_camera(None)
            return
        self.camera.stop()
        self._stopped_threads.append(self.camera_thread)
        self._camera_on = False
        self.window.set_camera(None)

    def shutdown(self) -> None:
        """세션 워커를 먼저 끝내고(마지막 슬롯 flush에 카메라가 필요) 카메라 스레드를 끝낸다."""
        self.workers.shutdown()
        self.camera.stop()
        deadline = time.monotonic() + CAMERA_JOIN_TIMEOUT_SEC
        for thread in [*self._stopped_threads, self.camera_thread]:
            if thread.is_alive():
                thread.join(max(0.0, deadline - time.monotonic()))
                if thread.is_alive():
                    logging.getLogger(__name__).warning("카메라 스레드가 끝나지 않은 채로 종료합니다")
        self._camera_on = False


def window_worker_factory(use_youtube: bool = True) -> WorkerFactory:
    """세션마다 창 추적 스레드를 만든다. 유튜브 카테고리 캐시는 앱 수명 동안 공유한다.

    공유하면 같은 영상은 API를 다시 부르지 않고, 이전 세션에 앞 화면에서 본 영상이
    다음 세션에 백그라운드로 재생돼도 카테고리를 알아 딴짓 규칙(점수 명세 2.1 2순위)에 걸린다.
    """
    youtube = YouTubeCategoryLookup() if use_youtube else None

    def create(slot_queue: queue.Queue, session_start: float) -> WindowTracker:
        return WindowTracker(slot_queue, session_start, use_domain_probe=True, use_media=True,
                             use_youtube=use_youtube, youtube=youtube)

    return create


def mode_gated(factory: WorkerFactory, enabled: Callable[[], bool]) -> WorkerFactory:
    """enabled()가 False인 세션에서는 워커를 만들지 않는다 (None, SessionWorkers가 건너뛴다)."""

    def create(slot_queue: queue.Queue, session_start: float):
        return factory(slot_queue, session_start) if enabled() else None

    return create


def cv_worker_factory(camera: CameraLink, window: MainWindow, camera_thread=None) -> WorkerFactory:
    """세션 시작 시점의 window.calibration(성공 또는 폴백 결과)에서 ear_baseline을 꺼내 넘긴다.

    camera_thread가 MockCameraThread면 mock 세션 워커를, 아니면 CvSessionWorker를 만든다.
    """

    def create(slot_queue: queue.Queue, session_start: float) -> CvSessionWorker | MockCvSessionWorker:
        assert window.calibration is not None
        baseline = window.calibration["ear_baseline"]
        if isinstance(camera_thread, CameraThread):
            return CvSessionWorker(camera, slot_queue, session_start, baseline, camera_thread=camera_thread)
        return MockCvSessionWorker(camera, slot_queue, session_start, baseline)

    return create


def build_app(data_dir: Path | None = None, use_youtube: bool = True,
              factories: list[WorkerFactory] | None = None,
              camera_thread_factory=MockCameraThread, scorer: ScoreFn | None = None,
              use_llm: bool = True) -> App:
    """창, 카메라 스레드, 세션 워커를 만들어 잇는다. QApplication을 먼저 만들어 둬야 한다.

    camera_thread_factory: CameraLink를 받아 시작 전 카메라 스레드를 돌려준다.
    scorer: 점수 함수. 기본은 LlmScorer.from_env(use_llm) — GUI 밖 스레드에서 돈다.
    """
    slot_queue: queue.Queue = queue.Queue()
    camera = CameraLink()
    camera_thread = camera_thread_factory(camera)
    window = MainWindow(
        camera=camera,
        slot_queue=slot_queue,
        score_session=scorer if scorer is not None else LlmScorer.from_env(use_llm),
        data_dir=data_dir,
        score_in_background=True,
    )
    workers = SessionWorkers(slot_queue, [])
    app = App(window, workers, camera, camera_thread, camera_thread_factory)
    if factories is None:
        def create_cv(slot_queue: queue.Queue, session_start: float):
            # 웹캠 모드를 껐다 켜면 카메라 스레드가 바뀌므로 세션을 시작할 때의 것을 쓴다
            return cv_worker_factory(app.camera, window, app.camera_thread)(slot_queue, session_start)

        factories = [mode_gated(create_cv, lambda: window.session_tracking.uses_camera),
                     mode_gated(window_worker_factory(use_youtube), lambda: window.session_tracking.uses_window)]
    workers.set_factories(factories)
    window.session_started.connect(workers.start)
    window.session_ending.connect(lambda _session_end: workers.stop())
    return app


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusPomo")
    parser.add_argument("--data-dir", type=Path, default=None, help="기록 저장 폴더 (기본: 레포의 data/)")
    parser.add_argument("--youtube", action=argparse.BooleanOptionalAction, default=True,
                        help="유튜브 카테고리 조회 (기본 켬, .env의 YOUTUBE_API_KEY 필요). --no-youtube로 끈다")
    parser.add_argument("--llm", action=argparse.BooleanOptionalAction, default=True,
                        help="LLM 창 분류·리포트 문구 (기본 켬, .env의 GEMINI_API_KEY 등 필요). --no-llm으로 끈다")
    parser.add_argument("--camera", type=int, default=0, help="웹캠 번호")
    parser.add_argument("--mock-camera", action="store_true", help="웹캠 없이 mock 카메라 스레드로 실행")
    parser.add_argument("--scenario", choices=tuple(CALIBRATION_SCENARIOS), default="success",
                        help="mock 캘리브레이션 시나리오 (--mock-camera)")
    parser.add_argument("--no-camera", action="store_true", help="mock 웹캠을 열지 못한 상태로 실행")
    parser.add_argument("--camera-lost-after", type=float, default=None, metavar="SEC",
                        help="mock 웹캠이 SEC초 뒤 끊겼다가 복구")
    args, qt_args = parser.parse_known_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def make_camera_thread(link: CameraLink) -> CameraThread | MockCameraThread:
        if not args.mock_camera:
            from cv_module.worker import WebcamEarSource
            return CameraThread(link, source=WebcamEarSource(args.camera))
        return MockCameraThread(link, scenario=args.scenario, has_camera=not args.no_camera,
                                lost_after_sec=args.camera_lost_after)

    qt_app = QApplication(sys.argv[:1] + qt_args)
    load_fonts()
    app = build_app(args.data_dir, args.youtube, camera_thread_factory=make_camera_thread, use_llm=args.llm)
    qt_app.aboutToQuit.connect(app.shutdown)
    app.start()
    app.window.show()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())