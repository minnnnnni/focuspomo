"""세션 동안 slot_queue에 쓰는 스레드(CV·창)를 세션 단위로 시작·종료한다 (INT-01, INT-02).

GUI 없이 테스트할 수 있도록 PySide6를 import하지 않는다.

- session_start는 워커를 만들 때 넘긴다. 공유 변수·시작 이벤트가 필요 없고, 세션마다 새 워커를 만든다.
- stop()은 신호만 보내고 기다리지 않는다 (메인 스레드에서 부르므로). 워커는 진행 중 슬롯을 큐에 넣고 끝난다.
- 한 워커를 만들지 못해도 세션은 계속한다. 그쪽 필드는 None("측정 불가")이 되어 감점되지 않는다.
- 웹캠은 session_start 전(캘리브레이션)부터 돌아야 하므로 CV 연결(INT-03)에서 별도 합의가 필요하다.
"""
from __future__ import annotations

import logging
import queue
import time
from collections.abc import Callable, Sequence
from typing import Protocol

logger = logging.getLogger(__name__)

JOIN_TIMEOUT_SEC = 2.0  # 앱을 끌 때 워커 전체가 끝나기를 기다리는 시간


class SessionWorker(Protocol):
    """threading.Thread 하위 클래스에 stop()을 더한 형태 (window_tracker.WindowTracker)."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


WorkerFactory = Callable[[queue.Queue, float], SessionWorker]  # (slot_queue, session_start) → 시작 전 워커


class SessionWorkers:
    def __init__(self, slot_queue: queue.Queue, factories: Sequence[WorkerFactory]) -> None:
        self.slot_queue = slot_queue
        self._factories = tuple(factories)
        self._running: list[SessionWorker] = []
        self._stopping: list[SessionWorker] = []

    @property
    def running_count(self) -> int:
        return len(self._running)

    def start(self, session_start: float) -> None:
        """새 세션의 워커를 시작한다. 이전 세션 워커가 돌고 있으면 먼저 멈춘다."""
        self.stop()
        self._stopping = [w for w in self._stopping if w.is_alive()]
        if self._stopping:
            # 끝나기 전에 flush하면 슬롯 번호 기준이 다른 메시지가 새 세션에 섞일 수 있다
            logger.warning("이전 세션 워커 %d개가 아직 끝나지 않았습니다", len(self._stopping))
        for factory in self._factories:
            try:
                worker = factory(self.slot_queue, session_start)
                worker.start()
            except Exception:
                logger.exception("세션 워커를 시작하지 못했습니다 (이 신호는 측정 불가로 기록)")
            else:
                self._running.append(worker)

    def stop(self) -> None:
        """돌고 있는 워커에 종료 신호를 보낸다. 기다리지 않는다."""
        for worker in self._running:
            try:
                worker.stop()
            except Exception:
                logger.exception("세션 워커 종료 신호를 보내지 못했습니다")
            self._stopping.append(worker)
        self._running = []

    def shutdown(self, timeout: float = JOIN_TIMEOUT_SEC) -> None:
        """앱 종료 시 부른다. 웹캠 등을 놓도록 전체 timeout초까지 기다린다."""
        self.stop()
        deadline = time.monotonic() + timeout
        for worker in self._stopping:
            worker.join(max(0.0, deadline - time.monotonic()))
        self._stopping = [w for w in self._stopping if w.is_alive()]
        if self._stopping:
            logger.warning("끝나지 않은 세션 워커 %d개를 두고 종료합니다", len(self._stopping))
