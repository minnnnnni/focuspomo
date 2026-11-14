"""집중도 점수 설정 (집중도 점수 산출 명세 6·7·8장).

점수 = 판정된 시간 중 집중한 시간의 비율(0~100). 가감점 가중치(구 scoring_weights.py)는 v4에서 폐기했다.
모두 임시값이며 명세 9장 검증 후 튜닝한다. 값을 바꿀 때는 명세 9장 표도 같은 PR에서 함께 고친다.
슬롯 길이(SLOT_SECONDS)는 schemas.py, CV·창 슬롯 압축 값은 aggregation.py에 있다.
"""

CAM_USABLE_RATIO = 0.3    # face_present가 측정된 시간이 세션의 이 비율 이상이면 웹캠 사용 가능 (7장)
MIN_JUDGED_RATIO = 0.3    # 판정 시간이 세션의 이 비율 미만이면 판정 데이터 부족 (6장)
MIN_JUDGED_SEC = 60       # 판정 시간이 이 초 미만이면 판정 데이터 부족 (6장)
TOP_DISTRACTIONS_N = 3    # 리포트에 보여줄 딴짓 항목 수 (8장)
BLINK_RATE_MIN_SEC = 60   # 얼굴 있던 시간이 이 초 미만이면 blinks_per_minute = None (8장)
