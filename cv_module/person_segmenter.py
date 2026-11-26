"""사람 영역 분리 (MediaPipe 이미지 세그멘터, selfie segmenter 모델) — CV-06.

얼굴 랜드마크는 얼굴이 정면에 가까워야 잡히지만, 사람 영역은 정수리·어깨만 보여도 남는다.
고개를 숙여 얼굴을 놓친 동안 자리에 있는지 보려고, 얼굴을 놓친 프레임에서만 돌린다 (CPU 부담을 줄이려고).

모델 파일(models/selfie_segmenter.tflite, 레포에 포함)이 없으면 경고 한 번만 남기고 None을 돌려준다.
그러면 얼굴을 놓친 구간은 일어남·졸다 떨굼만 가리고 나머지는 예전처럼 자리비움이 된다 (cv_module.head_down).
측정 도구(cv_module/measure_head_pose.py)를 돌리면 같은 경로에 받아진다.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "selfie_segmenter.tflite"
PERSON_PIXEL = 0.5  # 픽셀의 사람 확률이 이 이상이면 사람 영역


class PersonSegmenter:
    """BGR 프레임 → 화면에서 사람 영역 비율 (0~1). 모델이 없으면 None."""

    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH) -> None:
        self.model_path = Path(model_path)
        self._segmenter = None
        self._unavailable = False

    def _load(self) -> bool:
        if self._segmenter is not None:
            return True
        if self._unavailable:
            return False
        if not self.model_path.exists():
            logger.warning("사람 영역 모델이 없어 고개 숙임 중 자리 확인을 건너뜁니다: %s", self.model_path)
            self._unavailable = True
            return False
        try:
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision

            options = vision.ImageSegmenterOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(self.model_path)),
                running_mode=vision.RunningMode.IMAGE,  # 얼굴을 놓친 프레임에서만 띄엄띄엄 부르므로 IMAGE 모드
                output_confidence_masks=True,
                output_category_mask=False,
            )
            self._segmenter = vision.ImageSegmenter.create_from_options(options)
            return True
        except Exception:
            logger.exception("사람 영역 모델을 열지 못해 고개 숙임 중 자리 확인을 건너뜁니다")
            self._unavailable = True
            return False

    def ratio(self, frame_bgr) -> float | None:
        """예외가 나도 얼굴 판정(카메라)은 멈추지 않도록, 이 모델만 끄고 None을 돌려준다."""
        if not self._load():
            return None
        try:
            import cv2
            import mediapipe as mp

            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
            masks = self._segmenter.segment(image).confidence_masks
            if not masks:
                return None
            return float((masks[-1].numpy_view() >= PERSON_PIXEL).mean())  # 마스크가 여럿이면 마지막이 사람
        except Exception:
            logger.exception("사람 영역 계산 중 오류가 나 이 세션부터 고개 숙임 중 자리 확인을 건너뜁니다")
            self.close()
            self._unavailable = True
            return None

    def close(self) -> None:
        if self._segmenter is not None:
            self._segmenter.close()
            self._segmenter = None
