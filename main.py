"""FocusPomo 앱 진입점 (INT-01): slot_queue를 만들고 GUI와 세션 워커를 연결해 실행한다.

세션이 시작되면(session_started) 세션 워커를 session_start와 함께 만들어 시작하고,
세션이 끝나면(session_ending) 종료 신호를 보낸다. 앱을 끌 때 끝나기를 기다린다.

지금 실제로 연결된 것은 활성 창 추적뿐이다.
    캘리브레이션·CV 슬롯: mock (INT-03, 김시언 CV-05 이후 교체)
    점수: mock (INT-05, 신명철 SCORE-01~03 이후 교체)

유튜브 카테고리는 영상 ID를 YouTube API로 보내므로 프라이버시 문구(FIN-03) 전까지 --youtube로만 켠다.

    python main.py
    python main.py --youtube                 # 유튜브 카테고리 (.env의 YOUTUBE_API_KEY 필요)
    python main.py --data-dir C:\\temp\\fp-data  # 개발 중 기록을 다른 폴더에 저장
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.session_workers import SessionWorkers, WorkerFactory
from gui.main_window import MainWindow
from gui.style import load_fonts
from mocks.mock_calibration import MockCalibrator
from mocks.mock_cv_slots import MockCvWorker
from mocks.mock_scoring import MockScorer
from window_tracker.tracker import WindowTracker


def window_worker_factory(use_youtube: bool = False) -> WorkerFactory:
    def create(slot_queue: queue.Queue, session_start: float) -> WindowTracker:
        return WindowTracker(slot_queue, session_start, use_domain_probe=True, use_media=True,
                             use_youtube=use_youtube)

    return create


def build_app(data_dir: Path | None = None, use_youtube: bool = False,
              factories: list[WorkerFactory] | None = None) -> tuple[MainWindow, SessionWorkers]:
    """창과 세션 워커를 만들어 시그널로 잇는다. QApplication을 먼저 만들어 둬야 한다."""
    slot_queue: queue.Queue = queue.Queue()
    window = MainWindow(
        calibrator=MockCalibrator(),  # TODO(INT-03): CV 스레드 캘리브레이션으로 교체
        slot_queue=slot_queue,
        score_session=MockScorer(),  # TODO(INT-05): 실제 점수 모듈로 교체
        data_dir=data_dir,
    )
    if factories is None:
        # TODO(INT-03): MockCvWorker를 CV 스레드로 교체
        factories = [MockCvWorker, window_worker_factory(use_youtube)]
    workers = SessionWorkers(slot_queue, factories)
    window.session_started.connect(workers.start)
    window.session_ending.connect(lambda _session_end: workers.stop())
    return window, workers


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusPomo")
    parser.add_argument("--data-dir", type=Path, default=None, help="기록 저장 폴더 (기본: 레포의 data/)")
    parser.add_argument("--youtube", action="store_true", help="유튜브 카테고리 조회 (.env의 YOUTUBE_API_KEY 필요)")
    args, qt_args = parser.parse_known_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = QApplication(sys.argv[:1] + qt_args)
    load_fonts()
    window, workers = build_app(args.data_dir, args.youtube)
    app.aboutToQuit.connect(workers.shutdown)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
