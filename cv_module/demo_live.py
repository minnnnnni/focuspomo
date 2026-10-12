"""
웹캠으로 캘리브레이션 → 감김·졸음 판정 → 10초 슬롯 메시지를 실시간 확인하는 수동 테스트.

실행 (레포 루트에서):  python -m cv_module.demo_live
키: q 종료 / c 캘리브레이션 다시 / l 눈 랜드마크 on/off / m 전체 메시 on/off / s 화면 저장

랜드마크 표시: 노란 점 = EAR에 쓰는 눈 랜드마크 6점×2,
주황 가로선 = 눈 가로 길이(p1-p4), 빨간 세로선 = 눈 세로 길이(p2-p6, p3-p5),
저장한 이미지는 captures/ 에 쌓인다 (얼굴 사진이므로 커밋 금지).

실제 앱처럼 슬롯 메시지를 queue.Queue에 넣고, GUI 대신 이 스크립트가 꺼내서 출력한다.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path

import cv2

from config.aggregation import CALIBRATION_MIN_FACE_RATIO, CALIBRATION_SEC
from cv_module.calibration import Calibrator
from cv_module.drowsiness import is_drowsy
from cv_module.ear import LEFT_EYE, RIGHT_EYE, compute_ear
from cv_module.face_landmarks import FaceLandmarkDetector
from cv_module.pipeline import CvPipeline
from cv_module.second_summary import is_closed
from schemas import CvSecond, SlotMessage, closure

WHITE, YELLOW, GREEN, RED = (255, 255, 255), (0, 255, 255), (0, 200, 0), (0, 0, 255)
GRAY, ORANGE = (180, 180, 180), (0, 140, 255)
SAVE_DIR = Path("captures")


# ── 랜드마크 시각화 ──────────────────────────────────
def ipt(p) -> tuple[int, int]:
    return int(round(p[0])), int(round(p[1]))


def draw_mesh(frame, pts) -> None:
    for p in pts:
        cv2.circle(frame, ipt(p), 1, GRAY, -1)


def draw_eye(frame, pts, idx) -> None:
    p1, p2, p3, p4, p5, p6 = (ipt(pts[i]) for i in idx)
    cv2.line(frame, p1, p4, ORANGE, 1)       # 가로 길이
    cv2.line(frame, p2, p6, RED, 1)          # 세로 길이 1
    cv2.line(frame, p3, p5, RED, 1)          # 세로 길이 2
    for p in (p1, p2, p3, p4, p5, p6):
        cv2.circle(frame, p, 2, YELLOW, -1)


def draw_landmarks(frame, pts, show_mesh: bool) -> None:
    if show_mesh:
        draw_mesh(frame, pts)
    draw_eye(frame, pts, LEFT_EYE)
    draw_eye(frame, pts, RIGHT_EYE)


def save_frame(frame) -> None:
    SAVE_DIR.mkdir(exist_ok=True)
    path = SAVE_DIR / f"demo_{time.strftime('%Y%m%d_%H%M%S')}.png"
    cv2.imwrite(str(path), frame)
    print(f"[saved] {path}")


def put(frame, text: str, row: int, color=WHITE) -> None:
    # 한글 X 영어로
    cv2.putText(frame, text, (10, 30 + 28 * row),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)


def collect_second(session_start: float, lines: list[str]):
    # 1초 줄을 바로 찍지 않고 lines에 모아 둔다.
    def _cb(sec: CvSecond, run: int) -> None:
        t = sec["timestamp"] - session_start
        ear = f"{sec['ear_mean']:.3f}" if sec["ear_mean"] is not None else "  -  "
        lines.append(f"  t+{t:4.0f}s face={sec['face_present']!s:5} "
                     f"closed={sec['eyes_closed']!s:5} blink={sec['blink_count']} "
                     f"ear={ear} run={run}{'  << DROWSY' if is_drowsy(run) else ''}")
    return _cb


def print_slot(msg: SlotMessage) -> None:
    s = msg["slot"]
    ear = f"{s['ear_mean']:.3f}" if s["ear_mean"] is not None else "-"
    print(f"[SLOT {s['slot']:3d}] face_present={s['face_present']} drowsy={s['drowsy']} "
          f"closed_run_max={s['closed_run_max']} blink={s['blink_count']} "
          f"ear={ear} (seconds={len(msg['seconds'])})")
    print("-" * 72)


def main() -> None:
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise SystemExit("웹캠을 열 수 없습니다.")

    slot_queue: queue.Queue[SlotMessage] = queue.Queue()   # 실제 앱의 slot_queue 역할
    second_lines: list[str] = []                          # 출력 대기 중인 1초 줄
    calibrator = Calibrator(started_at=time.time())
    pipeline: CvPipeline | None = None
    show_landmarks, show_mesh = True, False

    with FaceLandmarkDetector() as detector:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            now = time.time()
            points = detector.detect(frame)
            ear = compute_ear(points) if points else None
            if points and show_landmarks:
                draw_landmarks(frame, points, show_mesh)

            if pipeline is None:
                # ── 1단계: 캘리브레이션 ──
                calibrator.add_frame(now, ear)
                if calibrator.done:
                    calib = calibrator.finish()
                    if (calib["face_detected_ratio"] < CALIBRATION_MIN_FACE_RATIO or calib["ear_baseline"] <= 0):
                        print(f"[calib] 실패: face ratio "
                              f"{calibrator.face_detected_ratio:.2f} → 다시 측정")
                        calibrator = Calibrator(started_at=now)
                    else:
                        print(f"[calib] ear_baseline={calib['ear_baseline']:.3f} "
                              f"valid_frames={calib['valid_frames']} "
                              f"ratio={calib['face_detected_ratio']:.2f}")
                        start = calib["ended_at"]          # = session_start
                        pipeline = CvPipeline(start, calib["ear_baseline"],
                                              on_second=collect_second(start, second_lines))
                else:
                    remain = CALIBRATION_SEC - (now - calibrator.started_at)
                    put(frame, f"CALIBRATING - look at the screen ({remain:.1f}s)", 0, YELLOW)
            else:
                # ── 2·3단계: 1초 요약 → 10초 슬롯 → 큐 ──
                for msg in pipeline.add_frame(now, ear):
                    slot_queue.put(msg)

                base = pipeline.ear_baseline
                put(frame, f"baseline {base:.3f}", 0)
                if ear is None:
                    put(frame, "NO FACE", 1, RED)
                else:
                    closed = is_closed(ear, base)
                    put(frame, f"EAR {ear:.3f}  closure {closure(ear, base):.2f}", 1)
                    put(frame, "CLOSED" if closed else "OPEN", 2, RED if closed else GREEN)
                put(frame, f"closed_run {pipeline.closed_run}s", 3)
                if is_drowsy(pipeline.closed_run):
                    put(frame, "DROWSY", 4, RED)

            # GUI 쪽 역할: 큐에서 슬롯 메시지를 꺼낸다 (끝난 슬롯 → 새 1초 순서로 출력)
            while not slot_queue.empty():
                print_slot(slot_queue.get())
            for line in second_lines:
                print(line)
            second_lines.clear()

            cv2.putText(frame, "PRESS 'q' to quit  'c' to calibrate  'l' for landmarks  'm' for mesh  's' to save",
                        (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GRAY, 1)
            cv2.imshow("FocusPomo CV demo", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("l"):
                show_landmarks = not show_landmarks
            if key == ord("m"):
                show_mesh = not show_mesh
            if key == ord("s"):
                save_frame(frame)
            if key == ord("c"):
                calibrator = Calibrator(started_at=time.time())
                pipeline = None

    if pipeline is not None:          # 세션 종료: 남은 1초와 마지막 슬롯까지 내보낸다
        last_slots = pipeline.finish()
        for line in second_lines:
            print(line)
        for msg in last_slots:
            print_slot(msg)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()