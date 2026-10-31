"""core/session_workers.py와 mocks.mock_cv_slots.MockCvWorker 테스트."""
import queue
import time
import unittest

from core.session_workers import SessionWorkers
from mocks.mock_cv_slots import FULL_FOCUS, MockCvWorker
from schemas import SLOT_SECONDS

T0 = 1_780_000_000.0
TIMEOUT = 5.0


class FakeWorker:
    def __init__(self, slot_queue, session_start, *, alive_after_stop=False, fail_stop=False):
        self.slot_queue = slot_queue
        self.session_start = session_start
        self.alive_after_stop = alive_after_stop
        self.fail_stop = fail_stop
        self.started = self.stopped = False
        self.join_timeouts: list[float | None] = []

    def start(self):
        self.started = True

    def stop(self):
        if self.fail_stop:
            raise RuntimeError("stop failed")
        self.stopped = True

    def join(self, timeout=None):
        self.join_timeouts.append(timeout)

    def is_alive(self):
        return self.started and (not self.stopped or self.alive_after_stop)


class Factory:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.created: list[FakeWorker] = []

    def __call__(self, slot_queue, session_start):
        worker = FakeWorker(slot_queue, session_start, **self.kwargs)
        self.created.append(worker)
        return worker


class SessionWorkersTest(unittest.TestCase):
    def setUp(self):
        self.q: queue.Queue = queue.Queue()

    def test_start_passes_queue_and_session_start(self):
        factory = Factory()
        workers = SessionWorkers(self.q, [factory])
        workers.start(T0)
        worker = factory.created[0]
        self.assertTrue(worker.started)
        self.assertIs(worker.slot_queue, self.q)
        self.assertEqual(worker.session_start, T0)
        self.assertEqual(workers.running_count, 1)

    def test_stop_signals_without_join(self):
        factory = Factory()
        workers = SessionWorkers(self.q, [factory])
        workers.start(T0)
        workers.stop()
        self.assertTrue(factory.created[0].stopped)
        self.assertEqual(factory.created[0].join_timeouts, [])
        self.assertEqual(workers.running_count, 0)

    def test_new_session_creates_new_workers_and_stops_old(self):
        factory = Factory()
        workers = SessionWorkers(self.q, [factory])
        workers.start(T0)
        workers.start(T0 + 100)
        self.assertEqual([w.session_start for w in factory.created], [T0, T0 + 100])
        self.assertTrue(factory.created[0].stopped)
        self.assertFalse(factory.created[1].stopped)

    def test_failed_factory_does_not_block_others(self):
        def broken(_q, _start):
            raise OSError("no device")

        factory = Factory()
        workers = SessionWorkers(self.q, [broken, factory])
        with self.assertLogs("core.session_workers", "ERROR"):
            workers.start(T0)
        self.assertEqual(workers.running_count, 1)

    def test_failed_stop_does_not_block_others(self):
        bad, good = Factory(fail_stop=True), Factory()
        workers = SessionWorkers(self.q, [bad, good])
        workers.start(T0)
        with self.assertLogs("core.session_workers", "ERROR"):
            workers.stop()
        self.assertTrue(good.created[0].stopped)

    def test_warns_when_previous_worker_still_alive(self):
        factory = Factory(alive_after_stop=True)
        workers = SessionWorkers(self.q, [factory])
        workers.start(T0)
        workers.stop()
        with self.assertLogs("core.session_workers", "WARNING"):
            workers.start(T0 + 100)

    def test_shutdown_stops_and_joins(self):
        factory = Factory()
        workers = SessionWorkers(self.q, [factory])
        workers.start(T0)
        workers.shutdown(timeout=1.0)
        worker = factory.created[0]
        self.assertTrue(worker.stopped)
        self.assertEqual(len(worker.join_timeouts), 1)
        self.assertLessEqual(worker.join_timeouts[0], 1.0)

    def test_shutdown_warns_about_stuck_worker(self):
        workers = SessionWorkers(self.q, [Factory(alive_after_stop=True)])
        workers.start(T0)
        with self.assertLogs("core.session_workers", "WARNING"):
            workers.shutdown(timeout=0.0)


class MockCvWorkerTest(unittest.TestCase):
    def test_pushes_finished_slots_and_flushes_partial_on_stop(self):
        q: queue.Queue = queue.Queue()
        session_start = time.time() - (2 * SLOT_SECONDS + 5)  # 슬롯 0·1은 끝났고 슬롯 2는 5초째
        worker = MockCvWorker(q, session_start, patterns=(FULL_FOCUS,))
        worker.start()
        first, second = q.get(timeout=TIMEOUT), q.get(timeout=TIMEOUT)
        worker.stop()
        worker.join(TIMEOUT)
        self.assertFalse(worker.is_alive())
        last = q.get(timeout=TIMEOUT)
        self.assertEqual([m["slot"]["slot"] for m in (first, second, last)], [0, 1, 2])
        self.assertEqual(len(first["seconds"]), SLOT_SECONDS)
        self.assertEqual(len(last["seconds"]), 5)
        self.assertTrue(q.empty())

    def test_stop_right_after_start_sends_nothing(self):
        q: queue.Queue = queue.Queue()
        worker = MockCvWorker(q, time.time())
        worker.start()
        worker.stop()
        worker.join(TIMEOUT)
        self.assertFalse(worker.is_alive())
        self.assertTrue(q.empty())


if __name__ == "__main__":
    unittest.main()
