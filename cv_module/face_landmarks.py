# MediaPipe Face Landmarker 래퍼 (VIDEO 모드, 얼굴 1개).

from __future__ import annotations

import time
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parent.parent / "models" / "face_landmarker.task"
)

Point = tuple[float, float]


class FaceLandmarkDetector:
    """
    BGR 프레임을 넣으면 얼굴 랜드마크의 픽셀 좌표 리스트를 돌려준다.

    얼굴이 없으면 None.
    """

    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH) -> None:
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts_ms = -1

    def detect(self, frame_bgr) -> list[Point] | None:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # VIDEO 모드는 timestamp(ms)가 호출마다 반드시 증가해야 한다.
        ts_ms = max(int(time.monotonic() * 1000), self._last_ts_ms + 1)
        self._last_ts_ms = ts_ms

        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.face_landmarks:
            return None

        # 정규화 좌표(0~1)는 x, y 스케일이 달라서 비율(EAR)이 왜곡된다.
        # 반드시 픽셀 좌표로 바꾼 뒤 거리를 계산한다.
        return [(lm.x * w, lm.y * h) for lm in result.face_landmarks[0]]

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> "FaceLandmarkDetector":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
