# FocusPomo 데이터 스키마 명세 v1

Sep 29, 2026 · @Someone

## 개요

모듈 간에 주고받는 데이터의 필드 이름·타입·값을 이 문서 하나로 고정한다. 개발계획서 v5의 2장 스키마를 기준으로 표기가 엇갈리던 부분을 통일했으며, 코드에서는 공용 `schemas.py`의 TypedDict로 정의한다.

데이터 흐름 순서:

1. **Calibration** — 세션 시작 직전, 화면을 응시하는 동안 개인별 눈 크기 기준값(`ear_baseline`)을 잡음 (김시언)
2. **CvSecond** — CV 스레드 내부에서 1초마다 만드는 요약. 스레드 밖으로 바로 내보내지 않음
3. **CvSlot** — CV 스레드가 10초마다 CvSecond 10개를 압축해 GUI 큐로 전송 (김시언)
4. **WindowSlot** — 창 스레드가 10초마다 활성 창을 조회해 GUI 큐로 전송 (신명철)
5. **MergedSlot** — GUI 메인 스레드가 같은 큐에서 두 메시지를 받아 `slot` 기준으로 하나의 슬롯 로그로 관리 (김민서)
6. **LlmSlotInput / LlmResponse** — 세션 종료 시 MergedSlot의 일부 필드만 LLM에 전달하고 라벨·리포트 문구를 받음 (신명철)
7. **SlotScore / SessionResult** — 점수 계산 결과, GUI 렌더링과 `sessions.jsonl` 저장에 그대로 사용
8. **FocusLogFile** — 검증·삭제용 세션별 원본 기록

슬롯 압축은 세션 종료 시 한꺼번에 하지 않고, 세션 중에 10초마다 CV 스레드 안에서 끝낸다. 따라서 세션 종료 시점에는 GUI가 이미 완성된 슬롯 로그를 갖고 있다.

## 공통 표기 규칙

| 항목 | 규칙 | 예시 |
| --- | --- | --- |
| 필드명 | snake\_case, 영어만 사용 | `face_present`, `app_name` |
| 시점 | 모든 시점 값은 `timestamp` (epoch 초, float, `time.time()`) | `1790753400.0` |
| 구간 | 구간이 필요한 출력에만 `start` / `end` (epoch 초) | `SlotScore.start`, `SlotScore.end` |
| 슬롯 번호 | 슬롯 단위 스키마에는 모두 `slot: int` 포함. 계산식 `int((timestamp - session_start) // 10)` | `slot: 0` \~ `149` |
| 세션 ID | 로컬 시각 기준 `YYYYMMDD-HHMMSS` 문자열. 파일명으로도 쓰므로 콜론 금지 | `"20260923-153000"` |
| 결측값 | 키는 절대 생략하지 않고 값만 `None` (JSON `null`). `None` = 측정 불가 | `face_present: None` |
| enum 값 | 데이터에는 영문 소문자 키, 한글은 GUI 표시용 매핑에서만 사용 | `"distraction"` → "딴짓" |
| 집계 접미사 | `_count` 횟수, `_total` 합계, `_mean` 평균, `_ratio` 0\~1 비율 | `drowsy_count`, `score_total`, `ear_mean` |
| 타입 표기 | Python 3.10+ 문법 (`float \| None`, `list[...]`) | `domain: str \| None` |
| 스키마 버전 | 파일에 저장되는 최상위 객체에 `schema_version: int` 포함. 필드 변경 시 +1 | `schema_version: 1` |
| 예약 필드 | 미구현 신호도 스키마에 넣고 `None`으로 채움 | `gaze_off_screen: None` |

## enum 값

**WindowLabel** — LLM이 슬롯마다 반환하는 창 분류

| 값 | GUI 표시 | 점수 영향 |
| --- | --- | --- |
| `work` | 작업 | 0 (중립) |
| `distraction` | 딴짓 | `distraction_penalty` 감점 |
| `unknown` | 미분류 | 0 (중립) |

**FinalTag** — 리포트 표에 보여줄 슬롯 대표 태그. 위에서부터 먼저 해당하는 하나만 표시하고, 점수는 모든 조건을 반영한다.

| 우선순위 | 값 | GUI 표시 | 조건 |
| --- | --- | --- | --- |
| 1 | `away` | 이탈(자리비움) | `face_present is False` |
| 2 | `drowsy` | 이탈(졸음) | `drowsy is True` |
| 3 | `distraction` | 이탈(딴짓) | `window_label == "distraction"` |
| 4 | `focus` | 집중 | `window_label == "work"` |
| 5 | `unknown` | 미분류 | 그 외 전부 (`window_label == "unknown"` 포함) |

LLM 응답에 위 세 값 외의 `window_label`이 오면 `unknown`으로 처리한다.

## 스키마별 필드

### 0. Calibration — 세션 시작 캘리브레이션 (CV 스레드, 세션마다 1회)

세션 시작 버튼을 누르면 `CALIBRATION_SEC`초 동안 화면을 응시하게 안내하고, 얼굴이 검출된 프레임의 EAR로 개인별 눈 크기 기준값을 잡는다. 25분 타이머와 `session_start`는 캘리브레이션이 끝난 뒤에 시작한다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `ear_baseline` | `float` | 응시 구간 EAR의 중앙값. 깜빡임 영향을 줄이려고 평균 대신 중앙값 사용 |
| `valid_frames` | `int` | 얼굴이 검출된 프레임 수 |
| `face_detected_ratio` | `float` | 전체 프레임 중 얼굴 검출 비율 (0\~1) |
| `started_at` | `float` | 캘리브레이션 시작 시각 |
| `ended_at` | `float` | 캘리브레이션 종료 시각 (= `session_start`) |
| `fallback_used` | `bool` | 캘리브레이션 실패로 직전 세션 기준값을 썼는지 |

실패 규칙: `face_detected_ratio` < `CALIBRATION_MIN_FACE_RATIO`이면 응시를 한 번 더 요청한다. 다시 실패하면 직전 세션의 `ear_baseline`을 쓰고 `fallback_used=True`로 둔다. 직전 값도 없으면 세션을 시작하지 않고 캘리브레이션을 다시 요구한다.

### 1. CvSecond — CV 1초 요약 (CV 스레드 내부, 1초마다)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `timestamp` | `float` | 해당 1초 구간의 시작 시각 |
| `face_present` | `bool` | 1초 동안 처리한 프레임의 과반에서 얼굴 검출 |
| `eyes_closed` | `bool` | 이 1초 동안 처리한 프레임이 모두 '감김'이었음. 얼굴 없으면 `False` |
| `blink_count` | `int` | 이 1초에 끝난 감김 중 길이가 `BLINK_MAX_SEC` 이하인 것의 수. 얼굴 없으면 0 |
| `ear_mean` | `float \| None` | 1초 평균 EAR (디버깅용). 얼굴 없으면 `None` |
| `gaze_off_screen` | `bool \| None` | 예약 필드. 미구현 동안 항상 `None` |

**눈 감김·졸음 판정 (임계값 방식)** — 프레임마다 개인 기준값 대비 감긴 정도를 계산한다.

```latex
\text{closure} = 1 - \frac{\text{EAR}}{\text{ear\_baseline}}
```

1. `closure` > `CLOSURE_THRESHOLD`인 프레임을 '감김'으로 본다.
2. 1초 동안 모든 프레임이 감김이면 그 초는 `eyes_closed=True`다.
3. CV 스레드는 `eyes_closed=True`가 연속된 초 수(`closed_run`)를 센다. 이 카운트는 **슬롯이 바뀌어도 초기화하지 않고** 이어서 센다. `eyes_closed=False`인 초나 얼굴이 없는 초가 오면 0으로 되돌린다.
4. 슬롯 안의 어느 초에서든 `closed_run`이 `DROWSY_MIN_RUN_SEC`(4) 이상이 되면 그 슬롯은 `drowsy=True`다.
5. 감김이 `BLINK_MAX_SEC` 이하로 끝나면 깜빡임 1회로 센다 (프레임 단위).

평균 비율(PERCLOS)은 쓰지 않는다. 경계에 걸친 졸음(예: 앞 슬롯 8\~9초 + 다음 슬롯 0\~1초)도 3번 규칙 덕분에 다음 슬롯에서 잡힌다. 1초 단위로 세므로 실제로는 4\~5초 이어진 감김부터 졸음으로 판정된다. 한 프레임만 튀어도 그 초가 감김에서 빠지므로, 검증에서 졸음을 너무 놓치면 2번을 '프레임의 90% 이상 감김'처럼 완화한다.

**판정 파라미터** (`config/aggregation.py`, 모두 임시값 — 7장 검증 후 튜닝)

| 상수 | 임시값 | 의미 |
| --- | --- | --- |
| `CALIBRATION_SEC` | 5 | 캘리브레이션 응시 시간(초) |
| `CALIBRATION_MIN_FACE_RATIO` | 0.7 | 캘리브레이션 성공 최소 얼굴 검출 비율 |
| `CLOSURE_THRESHOLD` | 0.4 | 기준 대비 이만큼 넘게 감기면 '감김' (EAR이 기준의 60% 미만) |
| `BLINK_MAX_SEC` | 0.5 | 이하로 끝난 감김 = 깜빡임 |
| `DROWSY_MIN_RUN_SEC` | 4 | `eyes_closed=True`가 이만큼 연속되면 졸음 (정수, 초) |
| `FACE_PRESENT_MIN_RATIO` | 0.5 | 슬롯에서 얼굴 있음으로 볼 최소 초 비율 |

### 2. WindowSlot — 활성 창 슬롯 (창 스레드 → GUI 큐, 10초마다)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `slot` | `int` | 창 스레드가 `slot_index(timestamp, session_start)`로 직접 계산 |
| `timestamp` | `float` | 조회 시각 (`session_start + 10*k`에 맞춰 조회) |
| `app_name` | `str \| None` | 예: `"VS Code"`, `"Chrome"`. 조회 실패 시 `None` |
| `domain` | `str \| None` | 브라우저일 때 도메인만 (예: `"youtube.com"`). 그 외·추출 실패 시 `None` |

활성 창은 10초에 한 번 조회하므로 압축 없이 슬롯 하나에 그대로 대응한다. 같은 슬롯이 두 번 오면 GUI는 `timestamp`가 더 늦은 메시지(= 나중 조회)를 남긴다. `timestamp`가 같으면 나중에 도착한 것을 남긴다.

### 3. CvSlot — 10초 압축 결과 (CV 스레드 → GUI 큐, 10초마다)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `slot` | `int` | 슬롯 번호 |
| `timestamp` | `float` | 슬롯 시작 시각 (`session_start + 10*slot`) |
| `face_present` | `bool \| None` | 얼굴 있는 초 비율 ≥ `FACE_PRESENT_MIN_RATIO`. 1초 요약이 0개면 `None` |
| `drowsy` | `bool` | 슬롯 안에서 `closed_run`이 `DROWSY_MIN_RUN_SEC` 이상이 된 초가 있으면 `True` (앞 슬롯에서 이어진 연속 포함) |
| `closed_run_max` | `int` | 이 슬롯 안에서 `closed_run`의 최댓값 (앞 슬롯에서 이어진 값 포함, 튜닝·리포트용) |
| `blink_count` | `int` | 1초 요약 `blink_count`의 합 (캡은 점수 단계에서 적용) |
| `ear_mean` | `float \| None` | 1초 `ear_mean`들의 평균 |
| `gaze_off_screen` | `bool \| None` | 예약 필드 |

### 3-1. SlotMessage — GUI 큐 메시지 (CV·창 공통)

CV 스레드와 창 스레드는 같은 `queue.Queue` 하나(`slot_queue`)에 메시지를 넣고, GUI 메인 스레드가 QTimer로 큐를 비우며 `source`로 구분한다.

| 메시지 | `source` | `slot` | 추가 필드 |
| --- | --- | --- | --- |
| `CvSlotMessage` | `"cv"` | `CvSlot` | `seconds: list[CvSecond]` — focus\_log 저장용 1초 원본 |
| `WindowSlotMessage` | `"window"` | `WindowSlot` | 없음 |

- 두 메시지는 도착 순서가 보장되지 않는다. GUI는 먼저 온 쪽으로 MergedSlot을 만들고 나중 것을 채운다.
- 세션이 끝나면 CV 스레드는 마지막 슬롯을 남은 초만으로 압축해 바로 보낸다.

### 4. MergedSlot — CvSlot + 창 정보 (병합 슬롯 로그)

GUI가 관리하는 세션 슬롯 로그의 한 줄이다. CvSlot의 모든 필드에 WindowSlot의 두 필드를 더한다. 세션 종료 시 한쪽 메시지가 끝내 오지 않은 슬롯은 기본값으로 채운다 — CV 쪽: `face_present=None`, `drowsy=False`, `closed_run_max=0`, `blink_count=0`, `ear_mean=None` / 창 쪽: `app_name=None`, `domain=None`. 양쪽 메시지가 모두 오지 않은 슬롯도 같은 기본값으로 채워 슬롯 로그가 0번부터 마지막 슬롯까지 빈칸 없이 이어지게 한다 ("판단할 근거 없음" → `final_tag="unknown"`, 점수 0).

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `app_name` | `str \| None` | WindowSlot에서 복사 |
| `domain` | `str \| None` | WindowSlot에서 복사 |

### 5. LlmSlotInput — LLM 요청의 슬롯 한 줄

MergedSlot에서 아래 필드만 골라 보낸다. `blink_count`, `closed_run_max`, `ear_mean`, `gaze_off_screen`은 보내지 않는다.

| 필드 | 타입 |
| --- | --- |
| `slot` | `int` |
| `timestamp` | `float` |
| `app_name` | `str \| None` |
| `domain` | `str \| None` |
| `face_present` | `bool \| None` |
| `drowsy` | `bool` |

### 6. LlmResponse — LLM 응답 (JSON 모드)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `slot_labels` | `list[{slot: int, window_label: WindowLabel}]` | 입력한 모든 슬롯에 대해 하나씩 |
| `report_text` | `str` | 한국어 1\~3문장 요약 |

응답에서 빠진 슬롯은 `unknown`으로 채운다.

### 7. SlotScore — 슬롯별 점수 (점수 모듈 → GUI)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `slot` | `int` | 슬롯 번호 |
| `start` | `float` | 슬롯 시작 시각 |
| `end` | `float` | 슬롯 끝 시각 (마지막 슬롯은 세션 종료 시각) |
| `window_label` | `WindowLabel` | LLM 분류. LLM 실패 시 `unknown` |
| `final_tag` | `FinalTag` | enum 우선순위로 결정 |
| `blink_count` | `int` | 캡 적용 전 원래 횟수 (표시용) |
| `score` | `int` | 개발계획서 2.5 공식으로 계산한 슬롯 점수 |

### 8. SessionResult — 세션 결과 (점수 모듈 → GUI, `data/sessions.jsonl` 한 줄)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `schema_version` | `int` | 현재 `1` |
| `session_id` | `str` | `YYYYMMDD-HHMMSS` |
| `session_start` | `float` | 세션 시작 시각 (= 캘리브레이션 종료 시각) |
| `session_end` | `float` | 세션 종료 시각 (중간 종료 포함) |
| `ear_baseline` | `float` | 이 세션에 쓴 눈 크기 기준값 |
| `slot_scores` | `list[SlotScore]` | 슬롯 순서대로 |
| `score_total` | `int` | 슬롯 `score` 합 |
| `blink_bonus_total` | `int` | 깜빡임 가점 합 (캡 적용 후) |
| `drowsy_count` | `int` | 졸음 감점 슬롯 수 |
| `away_count` | `int` | 자리 이탈 감점 슬롯 수 |
| `distraction_count` | `int` | 딴짓 감점 슬롯 수 |
| `report_text` | `str` | LLM 문구 또는 폴백 문구 |
| `llm_call_failed` | `bool` | `True`면 딴짓 감점 없이 계산됨 |

### 9. FocusLogFile — 세션별 원본 기록 (`data/focus_log/{session_id}.json`)

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `schema_version` | `int` | 현재 `1` |
| `session_id` | `str` | SessionResult와 동일 |
| `session_start` | `float` | 세션 시작 시각 |
| `calibration` | `Calibration` | 이 세션의 캘리브레이션 결과 전체 |
| `cv_seconds` | `list[CvSecond]` | CvSlotMessage의 `seconds`를 이어 붙인 1초 원본 (25분 기준 약 1,500개) |
| `merged_slots` | `list[MergedSlot]` | 병합 슬롯 로그 (25분 기준 150개) |

## 개발계획서 v5 대비 변경점

개발계획서 2장과 이 문서가 다르면 이 문서를 따른다. 합의 후 개발계획서도 같은 이름으로 고친다.

| 위치 | v5 표기 | 변경 후 | 이유 |
| --- | --- | --- | --- |
| 슬롯 압축 시점 (2.6) | 세션 종료 후 slot\_aggregator가 한꺼번에 압축 | CV 스레드가 세션 중 10초마다 압축해 GUI 큐로 전송 | 세션 종료 시 GUI가 이미 완성된 슬롯 로그를 가짐 |
| 스레드 → GUI 전달 | CV 1초, 창 10초 단위로 각자 push | 두 스레드 모두 10초 슬롯 단위로 같은 `slot_queue`에 push | GUI 한 곳에서 하나의 슬롯 로그로 관리 |
| 창 스레드 출력 | WindowSample (`slot` 없음) | WindowSlot (`slot` 직접 계산) | 슬롯 단위 메시지로 통일 |
| 졸음 판정 (2.1, 2.6) | `eyes_closed_ratio` 평균(PERCLOS) ≥ `DROWSY_PERCLOS_THRESHOLD` | 1초 전체 감김(`eyes_closed`)이 슬롯 경계를 넘어 4초 이상 연속되면 졸음 | 평균이 아닌 임계값 초과 + 지속 시간 방식 |
| CV 1초 요약 | `eyes_closed_ratio` | `eyes_closed: bool` | 같은 이유 |
| CV 슬롯 | `drowsy` 하나 | `drowsy`, `closed_run_max` | 연속 감김 길이를 튜닝·리포트 근거로 남김 |
| 캘리브레이션 | 프로그램 시작 시 1회 (선택) | 세션 시작마다 화면 응시로 `ear_baseline` 측정 | 조명·자세 변화에 맞춰 개인별 기준 갱신 |
| 세션 결과, focus\_log | (없음) | `ear_baseline`, `calibration` 추가 | 세션마다 기준값이 달라지므로 기록 |
| CV 1초 요약, 창 샘플 | `t` | `timestamp` | 시점 필드 이름 통일 |
| CV 슬롯 | `ear_value` | `ear_mean` | 1초 요약과 같은 값인데 이름이 달랐음 |
| CV·창 슬롯, slot\_scores | (없음) | `slot` 추가 | 병합·매칭 키를 하나로 고정 |
| CV 1초 요약, CV 슬롯 | 본문에만 언급 | `gaze_off_screen` 필드 명시 | 나중에 붙여도 인터페이스 유지 |
| `window_label` | `"작업"/"딴짓"/"미분류"` | `work`/`distraction`/`unknown` | 문자열 비교 오류 방지, GUI에서 한글 매핑 |
| `final_tag` | `"이탈(자리비움)"` 등 한글 | `away`/`drowsy`/`distraction`/`focus`/`unknown` | 같은 이유 |
| slot\_scores | `slot_score` | `score` | 이미 slot 객체 안에 있음 |
| slot\_scores | (없음) | `blink_count` 추가 | 리포트 표에서 점수 근거 표시 |
| 세션 결과 | `total_score` | `score_total` | 집계 접미사 규칙 |
| 세션 결과 | `face_away_count` | `away_count` | `final_tag` 값과 이름 맞춤 |
| 세션 결과 | (없음) | `schema_version`, `session_start`, `session_end` 추가 | 파일 호환성, 중간 종료 세션 구분 |
| `sessions.jsonl` 예시 | `llm_call_failed` 누락 | 포함 | 통계에서 폴백 세션 구분 |
| `session_id` | `"2026-09-23T15:30:00"` | `"20260923-153000"` | Windows 파일명에 콜론(`:`) 사용 불가 |

## schemas.py 초안

레포 루트에 `schemas.py`로 두고 모든 모듈과 mocks가 여기서 import한다. 필드를 바꿀 때는 이 파일과 이 문서를 같은 PR에서 함께 고친다.

```python
"""FocusPomo 공용 데이터 스키마 (schema_version 1)."""
from typing import Literal, TypedDict

SCHEMA_VERSION = 1
SLOT_SECONDS = 10

# ── enum ─────────────────────────────────────────────
WindowLabel = Literal["work", "distraction", "unknown"]
FinalTag = Literal["away", "drowsy", "distraction", "focus", "unknown"]

WINDOW_LABEL_KO: dict[str, str] = {
    "work": "작업",
    "distraction": "딴짓",
    "unknown": "미분류",
}
FINAL_TAG_KO: dict[str, str] = {
    "away": "이탈(자리비움)",
    "drowsy": "이탈(졸음)",
    "distraction": "이탈(딴짓)",
    "focus": "집중",
    "unknown": "미분류",
}

# ── 캘리브레이션 ─────────────────────────────────────
class Calibration(TypedDict):
    ear_baseline: float
    valid_frames: int
    face_detected_ratio: float
    started_at: float
    ended_at: float  # = session_start
    fallback_used: bool

# ── CV 스레드 내부 ───────────────────────────────────
class CvSecond(TypedDict):
    timestamp: float
    face_present: bool
    eyes_closed: bool  # 1초 전체가 감김
    blink_count: int
    ear_mean: float | None
    gaze_off_screen: bool | None  # 예약

# ── 스레드 → GUI 큐 (10초마다) ───────────────────────
class CvSlot(TypedDict):
    slot: int
    timestamp: float
    face_present: bool | None
    drowsy: bool
    closed_run_max: int  # 앞 슬롯에서 이어진 연속 포함
    blink_count: int
    ear_mean: float | None
    gaze_off_screen: bool | None  # 예약

class WindowSlot(TypedDict):
    slot: int
    timestamp: float
    app_name: str | None
    domain: str | None

class CvSlotMessage(TypedDict):
    source: Literal["cv"]
    slot: CvSlot
    seconds: list[CvSecond]

class WindowSlotMessage(TypedDict):
    source: Literal["window"]
    slot: WindowSlot

SlotMessage = CvSlotMessage | WindowSlotMessage

# ── GUI 슬롯 로그 ────────────────────────────────────
class MergedSlot(CvSlot):
    app_name: str | None
    domain: str | None

# ── LLM ──────────────────────────────────────────────
class LlmSlotInput(TypedDict):
    slot: int
    timestamp: float
    app_name: str | None
    domain: str | None
    face_present: bool | None
    drowsy: bool

class SlotLabel(TypedDict):
    slot: int
    window_label: WindowLabel

class LlmResponse(TypedDict):
    slot_labels: list[SlotLabel]
    report_text: str

# ── 점수 결과 ─────────────────────────────────────────
class SlotScore(TypedDict):
    slot: int
    start: float
    end: float
    window_label: WindowLabel
    final_tag: FinalTag
    blink_count: int
    score: int

class SessionResult(TypedDict):
    schema_version: int
    session_id: str  # "YYYYMMDD-HHMMSS"
    session_start: float
    session_end: float
    ear_baseline: float
    slot_scores: list[SlotScore]
    score_total: int
    blink_bonus_total: int
    drowsy_count: int
    away_count: int
    distraction_count: int
    report_text: str
    llm_call_failed: bool

class FocusLogFile(TypedDict):
    schema_version: int
    session_id: str
    session_start: float
    calibration: Calibration
    cv_seconds: list[CvSecond]
    merged_slots: list[MergedSlot]

# ── 헬퍼 ─────────────────────────────────────────────
EMPTY_CV_PART = {
    "face_present": None, "drowsy": False, "closed_run_max": 0,
    "blink_count": 0, "ear_mean": None,
    "gaze_off_screen": None,
}
EMPTY_WINDOW_PART = {"app_name": None, "domain": None}

def slot_index(timestamp: float, session_start: float) -> int:
    return int((timestamp - session_start) // SLOT_SECONDS)

def closure(ear: float, ear_baseline: float) -> float:
    """개인 기준 대비 눈이 감긴 정도. 0 = 기준만큼 뜸, 1 = 완전히 감음."""
    return 1.0 - ear / ear_baseline

def final_tag(face_present: bool | None, drowsy: bool, window_label: WindowLabel) -> FinalTag:
    if face_present is False:
        return "away"
    if drowsy:
        return "drowsy"
    if window_label == "distraction":
        return "distraction"
    if window_label == "work":
        return "focus"
    return "unknown"
```
