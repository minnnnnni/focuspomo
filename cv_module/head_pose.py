"""고개 각도: MediaPipe 얼굴 변환 행렬 → pitch·yaw·roll (도).

FaceLandmarker를 output_facial_transformation_matrixes=True로 만들면 얼굴마다 4×4 행렬을 준다.
MediaPipe의 3D 좌표는 오른손 좌표계이고 카메라가 원점에서 -z 쪽을 보며 y가 위쪽이다.
행렬의 회전 부분 R(왼쪽 위 3×3)의 열은 얼굴의 x(오른쪽)·y(위)·z(얼굴 앞쪽) 축을 카메라 좌표로 나타낸 것이므로,
얼굴 앞쪽 방향은 f = R[:, 2]다.

- pitch: 고개를 숙이면 +, 들면 −. 화면을 정면으로 보면 0 근처지만 카메라 높이에 따라 달라지므로
  판정에는 캘리브레이션 때 잰 평소 값과의 차이(pitch_rel)를 쓴다.
- yaw: 좌우로 돌림. 좌우 반전한 화면이면 부호가 바뀐다 (판정에는 쓰지 않음, 기록용).
- roll: 고개 갸웃 (기록용).

부호 규칙을 실제 웹캠으로 확인하지 못했으므로, 측정 스크립트에서 고개를 숙였을 때 pitch_rel이 +로 커지는지
먼저 보고 반대면 --flip-pitch로 뒤집는다 (cv_module/measure_head_pose.py).
"""
from __future__ import annotations

import math
from typing import Sequence


def head_angles(matrix: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    """4×4(또는 3×3) 얼굴 변환 행렬 → (pitch, yaw, roll) 도. pitch는 숙이면 +."""
    r = [[float(matrix[i][j]) for j in range(3)] for i in range(3)]
    fx, fy, fz = r[0][2], r[1][2], r[2][2]          # 얼굴 앞쪽 방향
    pitch = math.degrees(math.atan2(-fy, math.hypot(fx, fz)))  # 앞쪽이 아래(-y)로 기울면 +
    yaw = math.degrees(math.atan2(fx, fz))
    roll = math.degrees(math.atan2(r[1][0], r[0][0]))  # 얼굴 오른쪽 축이 위로 기운 정도
    return pitch, yaw, roll
