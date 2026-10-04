"""
Eye Aspect Ratio(EAR) 계산.

EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
p1, p4: 눈 양 끝 / (p2, p6), (p3, p5): 위아래 눈꺼풀 쌍
"""

from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]

# MediaPipe Face Mesh(478점) 인덱스, 순서는 p1..p6
LEFT_EYE = (33, 160, 158, 133, 153, 144)
RIGHT_EYE = (362, 385, 387, 263, 373, 380)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def eye_aspect_ratio(points: Sequence[Point], idx: Sequence[int]) -> float:
    p1, p2, p3, p4, p5, p6 = (points[i] for i in idx)
    horizontal = _dist(p1, p4)
    if horizontal == 0:
        return 0.0
    return (_dist(p2, p6) + _dist(p3, p5)) / (2.0 * horizontal)


def compute_ear(points: Sequence[Point]) -> float:
    # 양쪽 눈 EAR 평균. points는 픽셀 좌표여야 한다.
    return (eye_aspect_ratio(points, LEFT_EYE) + eye_aspect_ratio(points, RIGHT_EYE)) / 2.0
