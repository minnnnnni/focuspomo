"""FocusPomo 앱 진입점 (INT-01): 큐를 만들고 GUI, 카메라 스레드, 세션 워커를 연결해 실행한다.

세션이 시작되면(session_started) 세션 워커를 session_start와 함께 만들어 시작하고,
세션이 끝나면(session_ending) 종료 신호를 보낸다. 앱을 끌 때 끝나기를 기다린다.
카메라 스레드는 앱 수명 동안 돌고, GUI와 명령·이벤트 큐로만 주고받는다 (core.camera_link).

실제로 연결된 것: 웹캠 CV (cv_module.worker, CV-05), 활성 창 추적.
    점수: mock (INT-05, 신명철 SCORE-01~03 이후 교체)

유튜브 카테고리는 영상 ID를 YouTube API로 보내므로 프라이버시 문구(FIN-03) 전까지 --youtube로만 켠다.

    python main.py
    python main.py --youtube                 # 유튜브 카테고리 (.env의 YOUTUBE_API_KEY 필요)
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
from pathlib import Path
from typing import NamedTuple

from PySide6.QtWidgets import QApplication

from core.camera_link import CameraLink
from core.session_workers import SessionWorkers, WorkerFactory
from gui.main_window import MainWindow
from gui.style import load_fonts
from mocks.mock_calibration import SCENARIOS as CALIBRATION_SCENARIOS
from cv_module.worker import CameraThread, CvSessionWorker
from mocks.mock_camera import MockCameraThread, MockCvSessionWorker
from mocks.mock_scoring import MockScorer
from window_tracker.tracker import WindowTracker

CAMERA_JOIN_TIMEOUT_SEC = 2.0  # 앱을 끌 때 카메라 스레드가 웹캠을 놓기를 기다리는 시간


class App(NamedTuple):
    window: MainWindow
    workers: SessionWorkers
    camera: CameraLink
    camera_thread: CameraThread | MockCameraThread

    def start(self) -> None:
        self.camera_thread.start()

    def shutdown(self) -> None:
        """세션 워커를 먼저 끝내고(마지막 슬롯 flush에 카메라가 필요) 카메라 스레드를 끝낸다."""
        self.workers.shutdown()
        self.camera.stop()
        if self.camera_thread.is_alive():
            self.camera_thread.join(CAMERA_JOIN_TIMEOUT_SEC)
            if self.camera_thread.is_alive():
                logging.getLogger(__name__).warning("카메라 스레드가 끝나지 않은 채로 종료합니다")


def window_worker_factory(use_youtube: bool = False) -> WorkerFactory:
    def create(slot_queue: queue.Queue, session_start: float) -> WindowTracker:
        return WindowTracker(slot_queue, session_start, use_domain_probe=True, use_media=True,
                             use_youtube=use_youtube)

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


def build_app(data_dir: Path | None = None, use_youtube: bool = False,
              factories: list[WorkerFactory] | None = None,
              camera_thread_factory=MockCameraThread) -> App:
    """창, 카메라 스레드, 세션 워커를 만들어 잇는다. QApplication을 먼저 만들어 둬야 한다.

    camera_thread_factory: CameraLink를 받아 시작 전 카메라 스레드를 돌려준다.
    """
    slot_queue: queue.Queue = queue.Queue()
    camera = CameraLink()
    camera_thread = camera_thread_factory(camera)
    window = MainWindow(
        camera=camera,
        slot_queue=slot_queue,
        score_session=MockScorer(),  # TODO(INT-05): 실제 점수 모듈로 교체
        data_dir=data_dir,
    )
    if factories is None:
        factories = [cv_worker_factory(camera, window, camera_thread), window_worker_factory(use_youtube)]
    workers = SessionWorkers(slot_queue, factories)
    window.session_started.connect(workers.start)
    window.session_ending.connect(lambda _session_end: workers.stop())
    return App(window, workers, camera, camera_thread)


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusPomo")
    parser.add_argument("--data-dir", type=Path, default=None, help="기록 저장 폴더 (기본: 레포의 data/)")
    parser.add_argument("--youtube", action="store_true", help="유튜브 카테고리 조회 (.env의 YOUTUBE_API_KEY 필요)")
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
    app = build_app(args.data_dir, args.youtube, camera_thread_factory=make_camera_thread)
    qt_app.aboutToQuit.connect(app.shutdown)
    app.start()
    app.window.show()
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())