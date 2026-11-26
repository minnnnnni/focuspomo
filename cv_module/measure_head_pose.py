"""필기 자세 판정 기준을 정하기 위한 웹캠 측정 도구 (앱 동작과는 무관한 수동 측정, CV-06).

측정 순서와 결과 공유 방법은 docs/필기_자세_측정.md.

실행 (레포 루트에서, 가상환경 켠 뒤):
    python -m cv_module.measure_head_pose
    python -m cv_module.measure_head_pose --camera 1      # 다른 웹캠
    python -m cv_module.measure_head_pose --flip-pitch    # 고개를 숙였는데 각도가 −로 나올 때

순서: 5초 캘리브레이션(화면 응시) → 키로 상황을 고르며 동작 → q로 끝내면 CSV 저장 + 분석 요약 출력.
키:
    1 화면 보기        2 필기             3 졸다가 고개 떨굼
    4 숙인 채 졸기     5 숙인 채 일어나 나감  6 평소 자세에서 나감
    0 라벨 없음(전환 중)  f 각도 부호 뒤집기  c 캘리브레이션 다시  l 랜드마크 표시  q 끝내기

프레임마다 기록하는 값 (data/head_pose/head_pose_<시각>.csv, 영상·사진은 저장하지 않음):
    눈 비율(EAR)과 지금 규칙상 감김 여부, 고개 각도(평소 대비), MediaPipe 표정 점수(눈감음·시선아래·시선위),
    얼굴 위치·크기, 사람 영역 비율(정수리·어깨만 보여도 잡히는 사람 영역 분리 모델)

사람 영역 분리 모델(models/selfie_segmenter.tflite)이 없으면 처음 실행할 때 MediaPipe 공식 주소에서 받는다.
받지 못하면 그 값만 빼고 측정한다 (--no-segmenter로 끌 수도 있음).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

from config.aggregation import CALIBRATION_MIN_FACE_RATIO, CALIBRATION_SEC
from cv_module.analyze_head_pose import LABELS, analyze
from cv_module.ear import LEFT_EYE, RIGHT_EYE, compute_ear
from cv_module.face_landmarks import DEFAULT_MODEL_PATH as FACE_MODEL_PATH
from cv_module.head_pose import head_angles
from cv_module.second_summary import is_closed

MODELS_DIR = FACE_MODEL_PATH.parent
SEGMENTER_PATH = MODELS_DIR / "selfie_segmenter.tflite"
SEGMENTER_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
                 "selfie_segmenter/float16/latest/selfie_segmenter.tflite")
OUT_DIR = Path("data") / "head_pose"

KEYS = {ord("1"): "screen", ord("2"): "write", ord("3"): "nod", ord("4"): "doze_down",
        ord("5"): "leave_down", ord("6"): "leave", ord("0"): ""}
LABEL_EN = {"screen": "SCREEN", "write": "WRITE", "nod": "NOD OFF", "doze_down": "DOZE (HEAD DOWN)",
            "leave_down": "LEAVE FROM HEAD DOWN", "leave": "LEAVE", "": "(no label)"}
FIELDS = ["t", "label", "face", "ear", "ear_base", "ear_ratio", "closed", "pitch", "pitch_base", "pitch_rel",
          "yaw", "roll", "blink", "look_down", "look_up", "face_cx", "face_cy", "face_w", "person", "person_center"]

WHITE, YELLOW, GREEN, RED, GRAY = (255, 255, 255), (0, 255, 255), (0, 200, 0), (0, 0, 255), (180, 180, 180)


# ── 모델 ─────────────────────────────────────────────

class FaceProbe:
    """Face Landmarker + 표정 점수 + 얼굴 변환 행렬 (앱의 FaceLandmarkDetector와 같은 모델 파일)."""

    def __init__(self) -> None:
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(FACE_MODEL_PATH)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def detect(self, image: mp.Image, ts_ms: int, width: int, height: int) -> dict | None:
        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.face_landmarks:
            return None
        landmarks = result.face_landmarks[0]
        xs, ys = [lm.x for lm in landmarks], [lm.y for lm in landmarks]
        scores = {c.category_name: c.score for c in result.face_blendshapes[0]} if result.face_blendshapes else {}
        angles = head_angles(result.facial_transformation_matrixes[0]) \
            if result.facial_transformation_matrixes else (None, None, None)

        def pair(name: str) -> float | None:
            left, right = scores.get(f"{name}Left"), scores.get(f"{name}Right")
            return (left + right) / 2 if left is not None and right is not None else None

        return {
            "points": [(lm.x * width, lm.y * height) for lm in landmarks],  # EAR은 픽셀 좌표로 (ear.py)
            "pitch": angles[0], "yaw": angles[1], "roll": angles[2],
            "blink": pair("eyeBlink"), "look_down": pair("eyeLookDown"), "look_up": pair("eyeLookUp"),
            "face_cx": (min(xs) + max(xs)) / 2, "face_cy": (min(ys) + max(ys)) / 2, "face_w": max(xs) - min(xs),
        }

    def close(self) -> None:
        self._landmarker.close()


class PersonProbe:
    """사람 영역 분리 (selfie segmenter). 얼굴이 안 보여도 정수리·어깨가 보이면 사람 영역이 남는다."""

    def __init__(self, model_path: Path) -> None:
        options = vision.ImageSegmenterOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            output_confidence_masks=True,
            output_category_mask=False,
        )
        self._segmenter = vision.ImageSegmenter.create_from_options(options)
        self.inverted = False  # 모델이 배경 확률을 주는 경우 (캘리브레이션 때 정함)

    def mask(self, image: mp.Image, ts_ms: int):
        masks = self._segmenter.segment_for_video(image, ts_ms).confidence_masks
        if not masks:
            return None
        m = masks[-1].numpy_view()  # 마스크가 2개면 마지막이 사람
        return 1.0 - m if self.inverted else m

    @staticmethod
    def center_vs_edges(m) -> float:
        """가운데 세로 띠 평균 − 양쪽 가장자리 평균. 캘리브레이션(사람이 가운데) 때 +여야 정상."""
        w = m.shape[1]
        center = m[:, w // 3: 2 * w // 3].mean()
        edges = (m[:, : w // 8].mean() + m[:, -(w // 8):].mean()) / 2
        return float(center - edges)

    @staticmethod
    def ratios(m) -> tuple[float, float]:
        """(화면 전체에서 사람 영역 비율, 가운데 세로 띠에서 사람 영역 비율)"""
        w = m.shape[1]
        person = m > 0.5
        return float(person.mean()), float(person[:, w // 3: 2 * w // 3].mean())

    def close(self) -> None:
        self._segmenter.close()


def ensure_segmenter_model() -> bool:
    if SEGMENTER_PATH.exists():
        return True
    print(f"사람 영역 분리 모델을 받습니다: {SEGMENTER_URL}")
    try:
        with urllib.request.urlopen(SEGMENTER_URL, timeout=20) as resp:
            data = resp.read()
        SEGMENTER_PATH.write_bytes(data)
        print(f"저장: {SEGMENTER_PATH} ({len(data) // 1024} KB)")
        return True
    except Exception as exc:  # 네트워크 문제여도 측정은 계속 (사람 영역 값만 빠짐)
        print(f"모델을 받지 못했습니다 ({exc}). 위 주소를 브라우저로 받아 {SEGMENTER_PATH}에 두면 다음부터 씁니다.")
        print("이번에는 사람 영역 비율 없이 측정합니다.")
        return False


def open_camera(index: int):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(index)
    if not cap.isOpened():
        raise SystemExit(f"웹캠 {index}번을 열 수 없습니다. 다른 앱이 쓰고 있지 않은지, --camera 번호를 확인하세요.")
    return cap


# ── 화면 표시 ────────────────────────────────────────

def put(frame, text: str, row: int, color=WHITE, scale: float = 0.6) -> None:
    cv2.putText(frame, text, (10, 26 + 24 * row), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(frame, text, (10, 26 + 24 * row), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1)


def draw_eyes(frame, points) -> None:
    for idx in (LEFT_EYE, RIGHT_EYE):
        for i in idx:
            cv2.circle(frame, (int(points[i][0]), int(points[i][1])), 2, YELLOW, -1)


def fmt(value, spec: str) -> str:
    """화면 표시용 (없으면 -)."""
    return "-" if value is None else format(value, spec)


def cell(value, spec: str) -> str:
    """CSV 칸 (없으면 빈칸)."""
    return "" if value is None else format(value, spec)


# ── 측정 ─────────────────────────────────────────────

class Calibration:
    """평소 자세(화면 응시) 기준값: 눈 비율 중앙값, 각도 중앙값, 사람 영역 분리 방향."""

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.ears: list[float] = []
        self.pitches: list[float] = []
        self.seg_checks: list[float] = []
        self.frames = 0

    def add(self, ear: float | None, pitch: float | None, seg_check: float | None) -> None:
        self.frames += 1
        if ear is not None:
            self.ears.append(ear)
        if pitch is not None:
            self.pitches.append(pitch)
        if seg_check is not None:
            self.seg_checks.append(seg_check)

    def done(self, now: float) -> bool:
        return now - self.started_at >= CALIBRATION_SEC

    def face_ratio(self) -> float:
        return len(self.ears) / self.frames if self.frames else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="필기 자세 판정 기준 측정 (웹캠)")
    parser.add_argument("--camera", type=int, default=0, help="웹캠 번호 (기본 0)")
    parser.add_argument("--flip-pitch", action="store_true", help="고개 각도 부호 뒤집기 (숙였는데 −로 나올 때)")
    parser.add_argument("--no-segmenter", action="store_true", help="사람 영역 분리 모델 끄기")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"head_pose_{stamp}.csv"
    meta_path = csv_path.with_suffix(".json")
    sign = -1.0 if args.flip_pitch else 1.0

    face = FaceProbe()
    person = None
    if not args.no_segmenter and ensure_segmenter_model():
        try:
            person = PersonProbe(SEGMENTER_PATH)
        except Exception as exc:  # 모델 파일이 깨졌거나 버전이 안 맞아도 측정은 계속
            print(f"사람 영역 분리 모델을 열지 못했습니다 ({exc}). 사람 영역 비율 없이 측정합니다.")
    cap = open_camera(args.camera)

    print(__doc__.split("프레임마다")[0])
    print("먼저 5초 동안 평소처럼 화면을 봐 주세요 (캘리브레이션).")

    meta = {"created": stamp, "camera": args.camera, "flip_pitch": args.flip_pitch, "segmenter": person is not None,
            "seg_inverted": False, "calibrations": [], "fps_mean": None}
    calib: Calibration | None = Calibration(time.time())
    ear_base = pitch_base_raw = None
    label = ""
    label_since = time.time()
    record_start: float | None = None
    show_landmarks = True
    last_flush = 0
    last_ts_ms = -1
    frame_times: list[float] = []

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        while True:
            ok, frame = cap.read()
            if not ok:
                print("웹캠 프레임을 읽지 못했습니다. 끝냅니다.")
                break
            frame = cv2.flip(frame, 1)
            now = time.time()
            frame_times = (frame_times + [now])[-30:]
            h, w = frame.shape[:2]
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            ts_ms = max(int(time.monotonic() * 1000), last_ts_ms + 1)  # VIDEO 모드는 시각이 계속 늘어야 함
            last_ts_ms = ts_ms

            found = face.detect(image, ts_ms, w, h)
            ear = compute_ear(found["points"]) if found else None
            pitch = sign * found["pitch"] if found and found["pitch"] is not None else None
            mask = None
            if person is not None:
                try:
                    mask = person.mask(image, ts_ms)
                except Exception as exc:
                    print(f"사람 영역 분리에 실패해 끕니다 ({exc}). 나머지 값은 계속 기록합니다.")
                    person.close()
                    person = None
                    meta["segmenter"] = False
            if found and show_landmarks:
                draw_eyes(frame, found["points"])

            if calib is not None:
                # ── 캘리브레이션 ──
                calib.add(ear, pitch, PersonProbe.center_vs_edges(mask) if mask is not None else None)
                put(frame, f"CALIBRATING - look at the screen as usual ({CALIBRATION_SEC - (now - calib.started_at):.1f}s)",
                    0, YELLOW)
                if calib.done(now):
                    if calib.face_ratio() < CALIBRATION_MIN_FACE_RATIO or not calib.ears:
                        print(f"캘리브레이션 실패 (얼굴 검출 {calib.face_ratio():.0%}) — 다시 5초 화면을 봐 주세요.")
                        calib = Calibration(now)
                    else:
                        ear_base = statistics.median(calib.ears)
                        pitch_base_raw = sign * statistics.median(calib.pitches) if calib.pitches else 0.0
                        if person is not None and calib.seg_checks and statistics.median(calib.seg_checks) < 0:
                            person.inverted = not person.inverted
                            meta["seg_inverted"] = person.inverted
                        meta["calibrations"].append({"t": 0.0 if record_start is None else now - record_start,
                                                     "ear_baseline": ear_base, "pitch_baseline": sign * pitch_base_raw,
                                                     "face_ratio": calib.face_ratio()})
                        print(f"캘리브레이션 완료: 눈 기준값 {ear_base:.3f}, 평소 각도 {sign * pitch_base_raw:+.1f}°")
                        print("고개를 숙여 보세요. 화면의 pitch_rel이 +로 커지면 정상입니다 (−로 가면 f 키).")
                        print("그다음 1~6 키로 상황을 고르고 동작하세요. 상황 사이에는 0을 누르세요.")
                        calib = None
                        if record_start is None:
                            record_start = now
            else:
                # ── 기록 ──
                pitch_base = sign * pitch_base_raw
                pitch_rel = pitch - pitch_base if pitch is not None else None
                ratios = PersonProbe.ratios(mask) if mask is not None else (None, None)
                closed = ear is not None and is_closed(ear, ear_base)

                def val(key: str):
                    return found[key] if found else None

                writer.writerow({
                    "t": f"{now - record_start:.3f}", "label": label, "face": int(found is not None),
                    "ear": cell(ear, ".4f"), "ear_base": f"{ear_base:.4f}",
                    "ear_ratio": cell(ear / ear_base if ear is not None else None, ".3f"),
                    "closed": int(closed), "pitch": cell(pitch, ".2f"), "pitch_base": f"{pitch_base:.2f}",
                    "pitch_rel": cell(pitch_rel, ".2f"), "yaw": cell(val("yaw"), ".2f"), "roll": cell(val("roll"), ".2f"),
                    "blink": cell(val("blink"), ".3f"), "look_down": cell(val("look_down"), ".3f"),
                    "look_up": cell(val("look_up"), ".3f"), "face_cx": cell(val("face_cx"), ".3f"),
                    "face_cy": cell(val("face_cy"), ".3f"), "face_w": cell(val("face_w"), ".3f"),
                    "person": cell(ratios[0], ".3f"), "person_center": cell(ratios[1], ".3f"),
                })
                if int(now) != last_flush:  # 1초마다 디스크에 써 둔다 (중간에 꺼져도 남게)
                    f.flush()
                    last_flush = int(now)

                put(frame, f"REC {now - record_start:5.0f}s | {LABEL_EN[label]} ({now - label_since:.0f}s)", 0,
                    GREEN if label else GRAY)
                if found is None:
                    put(frame, "NO FACE", 1, RED)
                else:
                    put(frame, f"pitch_rel {fmt(pitch_rel, '+.1f')} deg  (now {fmt(pitch, '+.1f')}, usual "
                               f"{pitch_base:+.1f})  yaw {fmt(found['yaw'], '+.0f')}", 1)
                    put(frame, f"EAR {ear:.3f}  ratio {ear / ear_base:.2f}  -> {'CLOSED' if closed else 'OPEN'} "
                               "(current rule)", 2, RED if closed else GREEN)
                    put(frame, f"blink {fmt(found['blink'], '.2f')}  lookDown {fmt(found['look_down'], '.2f')}", 3)
                if ratios[0] is not None:
                    put(frame, f"person {ratios[0]:.2f}  center {ratios[1]:.2f}", 4)

            fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0]) if len(frame_times) > 1 else 0.0
            cv2.putText(frame, "1 screen  2 write  3 nod-off  4 doze-down  5 leave-down  6 leave  0 none",
                        (10, h - 34), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GRAY, 1)
            cv2.putText(frame, f"f flip pitch  c calibrate  l marks  q quit   ({fps:.0f} fps)",
                        (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, GRAY, 1)
            cv2.imshow("FocusPomo head pose measure", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key in KEYS and calib is None:
                label, label_since = KEYS[key], now
                print(f"[{now - record_start:6.1f}s] 상황: {LABELS.get(label, '라벨 없음')}")
            elif key == ord("f"):
                sign = -sign
                meta["flip_pitch"] = sign < 0
                if calib is not None:  # 캘리브레이션 중이면 섞이지 않게 처음부터
                    calib = Calibration(now)
                print(f"각도 부호를 뒤집었습니다 (flip_pitch={meta['flip_pitch']}).")
            elif key == ord("c"):
                calib, label = Calibration(now), ""
                print("캘리브레이션 다시: 5초 화면을 봐 주세요.")
            elif key == ord("l"):
                show_landmarks = not show_landmarks

    cap.release()
    cv2.destroyAllWindows()
    face.close()
    if person:
        person.close()

    if record_start is None:
        print("기록이 없습니다.")
        csv_path.unlink(missing_ok=True)
        return
    rows = sum(1 for _ in open(csv_path, encoding="utf-8")) - 1
    duration = time.time() - record_start
    meta["fps_mean"] = rows / duration if duration > 0 else None
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기록 저장: {csv_path} ({rows}프레임)\n")
    analyze(csv_path)


if __name__ == "__main__":
    main()
