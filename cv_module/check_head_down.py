"""고개 숙임 판정(CV-06) 확인 도구 — 화면 안내를 따라 1분 반 동안 움직이면 지금 규칙과 새 규칙 결과를 표로 보여 준다.

앱과 같은 코드(얼굴 검출 → CvPipeline → 세션 종료 후 소급 판정)를 쓴다. 영상·사진은 저장하지 않는다.

실행 (레포 루트에서, 가상환경 켠 뒤):
    python -m cv_module.check_head_down
    python -m cv_module.check_head_down --camera 1     # 다른 웹캠

순서 (단계가 바뀔 때마다 삐 소리, 터미널에도 안내):
    캘리브레이션 5초 → 화면 10초 → 필기 25초 → 화면 8초 → 졸다 고개 떨굼 12초 → 화면 8초
    → 일어나 화면 밖으로 15초 → 돌아와 화면 10초   (q: 중간에 끝내기)
끝나면 표 출력 + data/head_pose/check_<시각>.json 저장 (초마다 판정값만).
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from dataclasses import dataclass

import cv2

from config.aggregation import CALIBRATION_MIN_FACE_RATIO, CALIBRATION_SEC
from cv_module.calibration import Calibrator
from cv_module.drowsiness import is_drowsy
from cv_module.measure_head_pose import OUT_DIR, ensure_segmenter_model, open_camera, put
from cv_module.pipeline import CvPipeline
from cv_module.worker import TARGET_FPS, WebcamEarSource

PRESENT, DROWSY, AWAY, UNKNOWN = "자리에 있음", "졸음", "자리비움", "판정불가"
SKIP_SEC = 2  # 단계가 바뀐 직후 자세를 바꾸는 시간은 채점에서 뺀다
WHITE, YELLOW, GREEN, RED = (255, 255, 255), (0, 255, 255), (0, 200, 0), (0, 0, 255)


@dataclass(frozen=True)
class Step:
    key: str
    sec: int
    expected: str
    en: str
    ko: str


STEPS = [
    Step("screen1", 10, PRESENT, "Look at the screen", "평소처럼 화면 보기"),
    Step("write", 25, PRESENT, "Bow your head and WRITE on paper", "고개 숙여 노트에 필기 (삐 소리 나면 화면 보기)"),
    Step("screen2", 8, PRESENT, "Look at the screen", "화면 보기"),
    Step("nod", 12, DROWSY, "Close eyes 3s, then let your head DROP", "눈 감고 3초 버티다 고개 툭 떨구고 그대로 (삐 소리 나면 들기)"),
    Step("screen3", 8, PRESENT, "Look at the screen", "화면 보기"),
    Step("leave", 15, AWAY, "Stand up and WALK OUT of view", "일어나 화면 밖으로 나가기 (삐 소리 나면 돌아오기)"),
    Step("back", 10, PRESENT, "Come back, sit down, look at the screen", "돌아와 앉아 화면 보기"),
]
NAMES = {"screen1": "화면 보기", "write": "필기", "screen2": "화면 보기", "nod": "졸다 고개 떨굼",
         "screen3": "화면 보기", "leave": "일어나 나감", "back": "돌아와 화면"}


def beep() -> None:
    def ring() -> None:
        try:
            import winsound

            winsound.Beep(1200, 250)
        except Exception:
            print("\a", end="", flush=True)

    threading.Thread(target=ring, daemon=True).start()


def old_state(sec: dict, run: int) -> str:
    """지금 규칙: 얼굴이 없으면 자리비움."""
    if not sec["face_present"]:
        return AWAY
    return DROWSY if is_drowsy(run) else PRESENT


def new_state(sec: dict, run: int) -> str:
    """새 규칙: 세션 종료 후 붙은 posture까지 본다 (cv_module.slot_compressor와 같은 기준)."""
    posture = sec.get("posture")
    if posture == "nod":
        return DROWSY
    if sec["face_present"]:
        return DROWSY if is_drowsy(run) else PRESENT
    return {"head_down": PRESENT, "unseen": UNKNOWN}.get(posture, AWAY)


def counts(states: list[str]) -> str:
    order = [PRESENT, DROWSY, AWAY, UNKNOWN]
    return ", ".join(f"{s} {states.count(s)}" for s in order if states.count(s)) or "-"


def calibrate(cap, source: WebcamEarSource, window: str) -> Calibrator | None:
    print(f"\n[캘리브레이션] {CALIBRATION_SEC}초 동안 평소처럼 화면을 봐 주세요.")
    calib = Calibrator(time.time())
    while True:
        ok, frame = cap.read()
        if not ok:
            continue
        now = time.time()
        obs = source.observe(frame)
        calib.add_frame(now, obs.ear, obs.pitch)
        if calib.done:
            if calib.face_detected_ratio >= CALIBRATION_MIN_FACE_RATIO and calib.finish()["ear_baseline"] > 0:
                return calib
            print(f"  얼굴 검출 {calib.face_detected_ratio:.0%} — 다시 {CALIBRATION_SEC}초 화면을 봐 주세요.")
            calib = Calibrator(now)
            continue
        put(frame, f"CALIBRATING - look at the screen ({CALIBRATION_SEC - (now - calib.started_at):.1f}s)", 0, YELLOW, 0.7)
        put(frame, "face OK" if obs.ear is not None else "NO FACE", 1, GREEN if obs.ear is not None else RED)
        cv2.imshow(window, frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            return None


def main() -> None:
    parser = argparse.ArgumentParser(description="고개 숙임 판정 확인 (웹캠, 약 1분 반)")
    parser.add_argument("--camera", type=int, default=0, help="웹캠 번호 (기본 0)")
    args = parser.parse_args()

    has_segmenter = ensure_segmenter_model()
    cap = open_camera(args.camera)
    source = WebcamEarSource(args.camera)
    print("얼굴 검출 모델을 불러오는 중...")
    source.prepare()
    window = "FocusPomo head-down check"
    total = sum(s.sec for s in STEPS)
    print(f"\n안내를 따라 {total}초 동안 움직여 주세요. 단계가 바뀔 때마다 삐 소리가 납니다. (q: 끝내기)")
    for n, step in enumerate(STEPS, 1):
        print(f"  {n}. {NAMES[step.key]} {step.sec}초 — {step.ko}")

    try:
        calib = calibrate(cap, source, window)
        if calib is None:
            return
        raw = calib.finish()
        start = raw["ended_at"]
        pitch_base = calib.pitch_baseline
        live: list[tuple[dict, int]] = []  # 세션 중(소급 전) 1초 요약과 closed_run
        pipeline = CvPipeline(start, raw["ear_baseline"], lambda sec, run: live.append((dict(sec), run)),
                              pitch_baseline=pitch_base)

        bounds, t = [], 0
        for step in STEPS:
            bounds.append((t, t + step.sec, step))
            t += step.sec
        current, last_processed, face_txt = None, 0.0, ""
        beep()
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            now = time.time()
            elapsed = now - start
            if elapsed >= total:
                break
            idx = next(k for k, (lo, hi, _) in enumerate(bounds) if lo <= elapsed < hi)
            lo, hi, step = bounds[idx]
            if idx != current:
                if current is not None:
                    beep()
                current = idx
                print(f"[{idx + 1}/{len(STEPS)}] {NAMES[step.key]} {step.sec}초 — {step.ko}")
            if now - last_processed >= 1.0 / TARGET_FPS:  # 앱과 같은 처리 속도
                last_processed = now
                obs = source.observe(frame, with_person=True)
                pipeline.add_frame(now, obs.ear, **obs.extras())
                face_txt = "face OK" if obs.ear is not None else "no face"
                if obs.pitch is not None and pitch_base is not None:
                    face_txt += f"  pitch {obs.pitch - pitch_base:+.0f}"
                if obs.person is not None:
                    face_txt += f"  person {obs.person:.2f}"
            put(frame, f"{idx + 1}/{len(STEPS)}  {step.en}", 0, YELLOW, 0.7)
            put(frame, f"{hi - elapsed:4.1f}s left" + (f"   next: {bounds[idx + 1][2].en}" if idx + 1 < len(bounds) else ""), 1)
            put(frame, face_txt, 2, WHITE, 0.5)
            cv2.imshow(window, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("중간에 끝냈습니다. 끝낸 단계까지만 결과를 냅니다.")
                break
        beep()
        final = pipeline.finish()
    finally:
        cap.release()
        cv2.destroyAllWindows()
        source.close_estimator()

    # 소급 판정이 붙은 1초 요약 (다시 보낸 슬롯이 나중이므로 덮어쓴다)
    resolved = {}
    for msg in final:
        for sec in msg["seconds"]:
            resolved[sec["timestamp"]] = sec
    rows = []
    for sec, run in live:
        t_rel = sec["timestamp"] - start
        step = next((s for lo, hi, s in bounds if lo <= t_rel < hi), None)
        if step is None:
            continue
        lo = next(b[0] for b in bounds if b[2] is step)
        new_sec = resolved.get(sec["timestamp"], sec)
        rows.append({
            "t": round(t_rel, 1), "step": step.key, "scored": t_rel >= lo + SKIP_SEC,
            "face_present": sec["face_present"], "eyes_closed": sec["eyes_closed"], "closed_run": run,
            "ear_ratio": None if sec["ear_mean"] is None else round(sec["ear_mean"] / raw["ear_baseline"], 3),
            "pitch_rel": None if sec["pitch_rel"] is None else round(sec["pitch_rel"], 1),
            "face_y": None if sec["face_y"] is None else round(sec["face_y"], 3),
            "person_ratio": None if sec["person_ratio"] is None else round(sec["person_ratio"], 3),
            "person_max": None if sec["person_max"] is None else round(sec["person_max"], 3),
            "posture": new_sec.get("posture"),
            "old": old_state(sec, run), "new": new_state(new_sec, run),
        })

    print("\n| 단계 | 초 | 기대 | 지금 규칙 | 새 규칙 | 결과 |")
    print("|---|---|---|---|---|---|")
    summary = []
    for step in STEPS:
        scored = [r for r in rows if r["step"] == step.key and r["scored"]]
        if not scored:
            continue
        old = [r["old"] for r in scored]
        new = [r["new"] for r in scored]
        top = statistics.mode(new)
        verdict = "OK" if top == step.expected else "확인 필요"
        summary.append({"step": step.key, "expected": step.expected, "old": counts(old), "new": counts(new),
                        "verdict": verdict})
        print(f"| {NAMES[step.key]} | {len(scored)} | {step.expected} | {counts(old)} | {counts(new)} | {verdict} |")
    print(f"(단계마다 처음 {SKIP_SEC}초는 자세 바꾸는 시간이라 뺐습니다)")
    if not has_segmenter:
        print("사람 영역 모델이 없어 필기 구간은 예전처럼 자리비움으로 나옵니다 (models/selfie_segmenter.tflite 확인).")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"check_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({
        "ear_baseline": raw["ear_baseline"], "pitch_baseline": pitch_base, "segmenter": has_segmenter,
        "summary": summary, "seconds": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {out}  (이 파일을 공유하면 판정을 같이 볼 수 있습니다)")


if __name__ == "__main__":
    main()
