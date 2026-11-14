# FocusPomo 집중도 점수 산출 명세

2026-11-05 · 김민서 (v1 초안) · 2026-11-05 · 김민서 (v2: 기존 스키마·설정과 맞춤, 일시정지·짧은 세션 처리 보강) · 2026-11-05 · 김민서 (v2.1: 창 슬롯 우선순위 압축·백그라운드 재생 판정) · 2026-11-09 · 김민서 (v2.2: 유예 보정 폐기, 팀 합의)

> **상태: 팀 제안.** 확정되면 개발계획서 2.3~2.5절, 스키마 명세의 LLM·점수 결과 절, `schemas.py`, `config/`를 이 내용으로 바꾼다 (10장).

**v1 → v2 변경 요약**

| 항목 | v1 | v2 |
| --- | --- | --- |
| 태그·라벨 값 | 한글 (`"집중"`, `"작업"`) | 영문 키 (`"focus"`, `"work"`). 한글은 `*_KO`로 화면에서만 |
| 카메라·창 정상 여부 | 새 필드 `cam_ok`, `window_ok` | 기존 필드로 판단: `face_present is None`, `app_name is None` |
| 졸음 판정 | 60초 PERCLOS + 절대 EAR 기준 | **기존 합의 유지**: 캘리브레이션 기준 `closure` + 연속 감김 `DROWSY_MIN_RUN_SEC` |
| 점수·비율 계산 | 슬롯 개수 | **초 단위** (마지막 슬롯이 짧을 수 있음) |
| 유예 보정 이웃 | 리스트 인덱스 | **slot 번호가 연속**일 때만 이웃 (일시정지로 빠진 슬롯 고려) |
| 데이터 부족 기준 | `MIN_JUDGED_RATIO` 하나로 두 용도 | 웹캠 사용 가능 `CAM_USABLE_RATIO` / 판정 부족 `MIN_JUDGED_RATIO` + `MIN_JUDGED_SEC` |
| LLM 분류 키 | `(app, domain)` | `(app_name, domain, video_category)` |
| 창 슬롯 대표값 | 가장 오래 앞에 있던 창 | **우선순위**: 규칙상 명확한 딴짓 창이 `DISTRACTION_MIN_SEC` 이상이면 그 창, 아니면 가장 오래 앞에 있던 창 (v2.1) |
| 백그라운드 재생 | 점수 미반영 | 딴짓 카테고리 영상이 `BG_DISTRACTION_MIN_SEC` 이상 재생되면 앞 화면과 무관하게 딴짓 (v2.1) |
| 딴짓 판정 주체 | LLM만 | 명확한 딴짓은 **창 스레드가 로컬 규칙**으로, 나머지는 LLM (v2.1) |
| 출력 | 새 dict | 기존 `SessionResult`에서 점수 필드만 교체 (schema_version 4) |

## 1. 개요

집중도 점수는 **판정된 시간 중 집중한 시간의 비율(0~100점)**이다. 기존 계획서 2.5절의 슬롯별 가감점 합산을 대체한다.

- **측정 단위**: 10초 슬롯 (`schemas.SLOT_SECONDS`). 25분 세션 = 150슬롯.
- **점수 계산 시점**: 휴식 시작 시 1회, 코드에서 결정론적으로 계산한다. LLM은 창 분류와 리포트 문구만 맡는다.
- **눈 깜빡임**: 점수에서 제외하고 리포트 참고 지표로만 쓴다. 정상 깜빡임만으로 슬롯당 캡(+3)에 거의 항상 도달해 감점을 묻어버리고, 화면 집중 시 오히려 줄고 졸릴 때 늘어나는 경향이 있어 가점 방향이 맞지 않기 때문이다.
- **일시정지**: 정지 구간과 겹친 슬롯은 점수 모듈에 넘기기 전에 GUI가 뺀다 (스키마 명세 7-1, 변경 없음). 이 문서에서 "세션의 슬롯"은 정지 슬롯을 뺀 나머지이고, slot 번호는 중간이 빌 수 있다.

처리 순서:

1. **슬롯 압축**: 창 스레드가 1초 조회를 우선순위 규칙으로 10초 슬롯에 압축하고, 명확한 딴짓(앞 화면·백그라운드 재생)을 로컬 규칙으로 표시한다 (2.1).
2. **슬롯 로그**: CV·창 스레드가 보낸 슬롯을 GUI가 병합한다 (`MergedSlot`). 정지 슬롯을 뺀다.
3. **점수 모드 결정**: 웹캠·LLM 창 분류가 쓸 만한지 보고 모드를 정한다 (7장).
4. **LLM 창 분류**: 규칙에 걸리지 않은 슬롯의 고유 앱/도메인 목록을 분류받아 슬롯에 매핑한다 (5장).
5. **슬롯 태그**: 우선순위 규칙으로 슬롯마다 `raw_tag` 하나를 붙인다 (3장).
6. **점수 계산**: `final_tag`(= `raw_tag`, 유예 보정 없음 4장)로 집중 비율을 계산한다 (6장).

## 2. 슬롯 값

CV 쪽은 새 필드를 만들지 않고 `MergedSlot`의 기존 필드로 판정한다 (CV 스레드 인터페이스 변경 없음). 창 쪽은 슬롯 압축 규칙이 바뀌고 필드 하나(`rule_distraction`)가 추가된다 (2.1).

| 판정에 쓰는 값 | 필드 | 규칙 (기존 합의) |
| --- | --- | --- |
| 카메라 측정 불가 | `face_present is None` | 슬롯 안 1초 요약이 0개 (카메라 끊김, 또는 CV 메시지가 오지 않아 기본값으로 채운 슬롯) |
| 자리비움 | `face_present is False` | 얼굴 검출된 초의 비율이 `FACE_PRESENT_MIN_RATIO`(0.5) 미만 |
| 졸음 | `drowsy` | 캘리브레이션 기준 `closure > CLOSURE_THRESHOLD`인 감김이 `DROWSY_MIN_RUN_SEC`(4초) 이상 연속된 초가 있었음 |
| 깜빡임 | `blink_count` | 점수 미반영, 리포트 참고 지표 |
| 창 측정 불가 | `app_name is None` | 조회 실패, 또는 창 메시지가 오지 않은 슬롯 |
| 창 | `app_name`, `domain`, `video_category` | 슬롯의 대표 창 (2.1 우선순위 압축) |
| 규칙상 딴짓 | `rule_distraction` (신규) | 창 스레드가 로컬 규칙으로 판정한 명확한 딴짓. `"foreground"` / `"background"` / `None` (2.1) |

- 졸음은 v1 초안의 PERCLOS 대신 기존 규칙을 유지한다. 개인별 `ear_baseline`(캘리브레이션)을 그대로 쓰고, 60초 윈도우 때문에 졸음이 끝난 뒤에도 최대 6슬롯 동안 졸음으로 남는 문제가 없다. PERCLOS는 9장 검증에서 비교 후보로만 둔다.
- 카메라가 슬롯 일부만 끊긴 경우(예: 10초 중 2초만 수신)는 받은 초만으로 `face_present`를 정한다. 이게 오탐을 많이 내면 CV 쪽에 "수신된 초 비율" 기준을 추가하는 것을 검토한다 (9장).

### 2.1 창 슬롯 우선순위 압축 (창 스레드, 신명철)

창 스레드는 지금처럼 1초마다 앞 화면 창과 미디어 재생 상태를 조회한다. 10초 슬롯으로 압축할 때 **가장 오래 앞에 있던 창 하나로 대표하지 않고, 명확한 딴짓을 우선한다.** 유튜브 게임 영상처럼 딴짓이 분명한 창은 몇 초만 봐도 그 슬롯의 성격을 정하는데, "가장 오래" 규칙으로는 VS Code 6초 + 유튜브 4초 슬롯이 작업으로만 남기 때문이다.

**명확한 딴짓 규칙** (로컬, `config/window_rules.py`): 아래 중 하나에 해당하는 창. 규칙에 없는 창은 지금처럼 세션 종료 후 LLM이 분류한다.

- `video_category`가 `DISTRACTION_VIDEO_CATEGORIES`에 있음: `"Gaming"`, `"Entertainment"`, `"Comedy"`, `"Film & Animation"`, `"Sports"`, `"People & Blogs"` (2026-11-09 결정)
- `domain`이 `DISTRACTION_DOMAINS`에 있음 (예: 숏폼·SNS·OTT 도메인. 목록은 팀이 정함)

`"Music"`, `"Education"`, `"Science & Technology"` 같은 카테고리는 규칙에 넣지 않는다. 공부하며 듣는 음악, 강의 영상은 딴짓이 아닐 수 있으므로 LLM 판단(앞 화면) 또는 무시(백그라운드)로 둔다. 특히 `"Music"`은 백그라운드로 틀어 두는 경우가 많아 딴짓에서 뺀다 (2026-11-09 결정).

**슬롯 압축 우선순위** — 위에서부터 처음 해당하는 것으로 정한다.

| 순위 | 조건 | 대표 창 (`app_name`·`domain`·`video_category`) | `rule_distraction` |
| --- | --- | --- | --- |
| 1 | 앞 화면에서 규칙상 딴짓 창이 `DISTRACTION_MIN_SEC`(3초) 이상 | 규칙상 딴짓 창 중 가장 오래 앞에 있던 것 | `"foreground"` |
| 2 | 백그라운드 재생 중인 영상의 `background_category`가 `DISTRACTION_VIDEO_CATEGORIES`에 있고 그 시간이 `BG_DISTRACTION_MIN_SEC`(5초) 이상 | 조회에 성공한 창 중 가장 오래 앞에 있던 것 | `"background"` |
| 3 | 그 외 | 조회에 성공한 창 중 가장 오래 앞에 있던 것 (전부 실패면 `None`) | `None` |

```python
def is_rule_distraction(domain: str | None, video_category: str | None) -> bool:
    return domain in DISTRACTION_DOMAINS or video_category in DISTRACTION_VIDEO_CATEGORIES

def build_window_slot(slot: int, samples: list[WindowSample]) -> WindowSlot:
    fg_hits = [s for s in samples if is_rule_distraction(s["domain"], s["video_category"])]
    bg_hits = [s for s in samples
               if s["background_media"] and s["background_category"] in DISTRACTION_VIDEO_CATEGORIES]
    measured_fg = [s for s in samples if s["app_name"] is not None] or samples
    if len(fg_hits) * SAMPLE_INTERVAL_SEC >= DISTRACTION_MIN_SEC:
        pool, rule = fg_hits, "foreground"
    elif len(bg_hits) * SAMPLE_INTERVAL_SEC >= BG_DISTRACTION_MIN_SEC:
        pool, rule = measured_fg, "background"
    else:
        pool, rule = measured_fg, None
    # pool 안에서 대표 창을 고르는 방법(_most_common)과 background_media_sec·background_category는 기존 그대로
    ...
```

- 앞 화면 딴짓이 백그라운드 딴짓보다 우선한다. 둘 다 해당해도 `"foreground"` 하나만 기록한다.
- 앞 화면에 보이는 영상은 기존 `media_on_screen()` 규칙대로 백그라운드로 세지 않으므로, 같은 영상이 앞 화면·백그라운드에서 두 번 세지지 않는다.
- 백그라운드 카테고리를 모르면(`background_category is None`: 앞 화면에서 조회한 적 없는 영상, `--youtube` 꺼짐 등) 규칙에 걸리지 않는다. 기존 원칙대로 불확실하면 감점 쪽으로 세지 않는다.
- 3순위의 "조회에 성공한 창 우선"은 잠금 화면·작업 전환처럼 조회가 실패한 초가 가장 많아도, 실제 창이 있었으면 그 창으로 대표하기 위해서다.
- **유튜브 카테고리 조회는 기본으로 켠다** (2026-11-09 결정, 계획서 v9의 "`--youtube`로만 켬"을 바꿈). 카테고리 규칙이 앞 화면·백그라운드 판정의 핵심이기 때문이다. 영상 ID가 YouTube Data API로 나가므로 README·개인정보 안내에 적고, 끄는 옵션(예: `--no-youtube`)은 남긴다. API 키가 없거나 조회에 실패하면 카테고리가 None이 되어 카테고리 규칙만 빠지고 도메인 규칙·LLM 분류는 그대로 동작한다.
- 판정은 로컬에서만 하고, 규칙 목록과 결과 외에 창 제목·영상 제목은 기존처럼 저장하거나 내보내지 않는다.

## 3. 슬롯 태그 판정 (`raw_tag`)

슬롯마다 태그는 **하나만** 붙고, 위에서부터 처음 해당하는 조건이 이긴다. 한 슬롯이 두 번 감점되는 일이 없다 (예: 자리를 비웠는데 화면에 유튜브가 떠 있어도 자리비움 한 번).

| 우선순위 | 태그 (`FinalTag`) | 화면 표시 | 조건 | 점수 계산 시 |
| --- | --- | --- | --- | --- |
| 1 | `no_cam` | 판정불가 | `face_present is None` | 분모 제외 |
| 2 | `away` | 이탈(자리비움) | `face_present is False` | 이탈 (분모 포함) |
| 3 | `drowsy` | 이탈(졸음) | `drowsy` | 이탈 (분모 포함) |
| 4 | `distraction` | 이탈(딴짓) | 창 라벨 = `distraction` | 이탈 (분모 포함) |
| 5 | `focus` | 집중 | 창 라벨 = `work` | 집중 (분자·분모 포함) |
| 6 | `unknown` | 미분류 | 그 외 (창 라벨 = `unknown`. `app_name is None`이면 라벨이 항상 `unknown`) | 분모 제외 |

```python
JUDGED = ("focus", "away", "drowsy", "distraction")
AWAY = ("away", "drowsy", "distraction")

def raw_tag(slot: MergedSlot, window_label: WindowLabel, mode: ScoreMode) -> FinalTag:
    if mode != "window_only":
        if slot["face_present"] is None:
            return "no_cam"
        if slot["face_present"] is False:
            return "away"
        if slot["drowsy"]:
            return "drowsy"
        if mode == "cam_only":
            return "distraction" if window_label == "distraction" else "focus"  # 규칙상 딴짓만 남음
    if window_label == "distraction":
        return "distraction"
    if window_label == "work":
        return "focus"
    return "unknown"
```

**창 라벨(`window_label`) 결정**: `rule_distraction`이 None이 아니면 `"distraction"`, 아니면 LLM 라벨(5장). 규칙 판정은 LLM보다 우선하며 LLM 결과로 뒤집지 않는다.

```python
def window_label_of(slot: MergedSlot, llm_labels: dict) -> WindowLabel:
    if slot["rule_distraction"] is not None:
        return "distraction"
    if slot["app_name"] is None:
        return "unknown"
    return llm_labels.get((slot["app_name"], slot["domain"], slot["video_category"]), "unknown")
```

- 순서 근거: 얼굴이 없으면 졸음·창 정보가 의미 없고, 졸고 있으면 어떤 창이 떠 있든 집중이 아니다.
- `app_name is None`인 슬롯은 LLM에 보내지 않고 라벨을 `unknown`으로 둔다. 그래서 "창 측정 불가"를 따로 검사하지 않아도 6순위로 떨어진다.
- 백그라운드 딴짓 슬롯(`rule_distraction == "background"`)도 4순위 `distraction`이다. 다만 자리비움·졸음이 먼저 이기므로, 자리를 비운 채 영상이 재생되면 자리비움이다.
- `mode` 분기는 7장 폴백이다. `cam_only`에서도 규칙상 딴짓은 로컬 판정이라 그대로 쓴다.

## 4. 유예 보정 없음 (`final_tag`)

**짧은 이탈을 집중으로 바꾸는 유예 보정은 하지 않는다 (2026-11-09 팀 합의로 폐기). `final_tag`는 항상 `raw_tag`와 같다.**

- v2 초안은 앞뒤가 집중인 10초 이하 이탈을 집중으로 보정했다. 그러나 `집중·졸음·집중`처럼 실제 이탈도 집중으로 계산되고, 10초씩 번갈아 딴짓하면 전부 집중으로 보정되는 문제가 있어 팀 합의 없이 넣지 않기로 했다.
- 순간 노이즈는 점수 단계가 아니라 신호 단계에서 거른다: 졸음은 연속 감김 `DROWSY_MIN_RUN_SEC`, 자리비움은 `FACE_PRESENT_MIN_RATIO`, 앞 화면 규칙상 딴짓은 `DISTRACTION_MIN_SEC` (2장, 2.1).
- `SlotScore`의 `raw_tag`·`final_tag` 두 필드는 스키마 변경 없이 그대로 두고 같은 값을 넣는다. 검증(9장) 후 보정을 다시 도입하면 `final_tag`만 바꾸면 된다.

## 5. LLM 창 분류

LLM에는 슬롯 로그 전체가 아니라 **세션에 등장한 고유 창 항목 목록**을 보내 한 번에 분류받고, 결과를 코드에서 슬롯에 매핑한다. 같은 앱이 슬롯마다 다르게 분류되는 일이 없고, 전송량도 슬롯 수와 무관해진다. 슬롯별 `face_present`·`drowsy`는 더 이상 LLM에 보내지 않는다.

1. 세션 종료 시 정지 슬롯을 뺀 슬롯 중 `app_name`이 None이 아니고 `rule_distraction`이 None인 것만 골라 `(app_name, domain, video_category)`로 중복 제거하고, 항목마다 등장 슬롯 수를 붙인다. 규칙상 딴짓 슬롯은 이미 라벨이 정해졌으므로 보내지 않는다.
   - `video_category`는 유튜브 영상일 때만 값이 있다 (`--youtube` 실행 시). 같은 youtube.com이라도 `"Education"`과 `"Gaming"`을 따로 분류받기 위해 키에 넣는다. 꺼져 있으면 None이라 v1과 같아진다.
2. LLM은 항목마다 `work` / `distraction` / `unknown` 중 하나를 반환한다. 판단이 애매하면 `unknown`을 반환하도록 프롬프트에 명시한다. 응답 라벨은 `schemas.normalize_window_label()`로 검증한다.
3. 코드는 3장 `window_label_of()`로 매핑한다. 규칙상 딴짓은 `distraction`, 응답에 빠진 항목과 `app_name is None`인 슬롯은 `unknown`이다.

```json
// 요청
{"items": [
  {"app_name": "Code.exe", "domain": null, "video_category": null, "slots": 92},
  {"app_name": "chrome.exe", "domain": "stackoverflow.com", "video_category": null, "slots": 18},
  {"app_name": "chrome.exe", "domain": "youtube.com", "video_category": "Gaming", "slots": 15}
]}

// 응답
{"item_labels": [
  {"app_name": "Code.exe", "domain": null, "video_category": null, "window_label": "work"},
  {"app_name": "chrome.exe", "domain": "stackoverflow.com", "video_category": null, "window_label": "work"},
  {"app_name": "chrome.exe", "domain": "youtube.com", "video_category": "Gaming", "window_label": "distraction"}
]}
```

- 리포트 문구(`report_text`)는 점수 계산이 끝난 뒤 생성한다. 입력은 태그별 시간, 점수, 점수 모드, 주요 딴짓 항목, 최장 연속 집중 시간 같은 집계값만 넘긴다.
- 얼굴·졸음 등 웹캠 신호의 원값과 영상은 외부로 보내지 않는다.
- 분류 호출과 문구 생성 호출을 하나로 합칠지, 문구를 로컬 템플릿으로 만들지, 실패 판정 기준(타임아웃·재시도)은 담당자(신명철)가 정한다. 분류 호출이 실패하면 `llm_call_failed = True`.

## 6. 점수 계산

점수는 `final_tag` 기준으로, **판정된 시간 중 집중 시간의 비율**을 100점 만점으로 환산한 값이다. 이탈 종류(자리비움·졸음·딴짓)는 모두 같은 무게로 센다.

**시간은 슬롯 개수가 아니라 초로 센다.** 슬롯의 길이는 `end - start`이고, 보통 10초지만 마지막 슬롯은 `session_end`에서 끝나 더 짧을 수 있다. 태그별 시간(`tag_seconds`)과 점수가 같은 기준을 쓰게 하기 위해서다.

```
judged_sec  = sec(focus) + sec(away) + sec(drowsy) + sec(distraction)
focus_score = floor( 100 × sec(focus) / judged_sec + 0.5 )
```

- 분모 제외: `unknown`, `no_cam`.
- 반올림은 사사오입(`floor(x + 0.5)`)으로 정수를 만든다. Python `round()`는 78.5 → 78로 내리는 은행원 반올림이라 쓰지 않는다.
- 동일 가중을 쓰는 이유: "판정된 시간 중 집중한 시간의 비율"이라는 한 문장으로 설명되고, 근거를 대기 어려운 가중치 값이 필요 없다. 이탈 종류별 차이는 점수에 섞지 않고 리포트에서 따로 보여준다.

**판정 데이터 부족**: 아래 중 하나라도 해당하면 `focus_score = None`, `score_note = "판정 데이터 부족"`으로 둔다. 모드와 상관없다.

- `judged_sec < MIN_JUDGED_RATIO × total_sec` (판정된 시간이 세션의 30% 미만)
- `judged_sec < MIN_JUDGED_SEC` (판정된 시간이 60초 미만. 일찍 끝낸 아주 짧은 세션에서 슬롯 몇 개로 0점·100점이 나오는 것을 막음)

`total_sec`는 정지 슬롯을 뺀 슬롯 길이의 합이다.

**미분류가 많은 세션의 점수 해석**: 미분류를 분모에서 빼기 때문에 "작업 45슬롯 + 미분류 105슬롯"이면 100점이 된다. 점수 자체는 "판정된 시간 중 비율"로 맞는 값이므로 규칙은 바꾸지 않되, 리포트에 **판정된 시간(`judged_seconds`)을 점수 바로 옆에 항상 표시**해 몇 분을 기준으로 낸 점수인지 보이게 한다 (8장).

계산 예시 (25분 세션, 150슬롯, 정지 없음):

| final_tag | 슬롯 수 | 시간 | 분모 포함 |
| --- | --- | --- | --- |
| focus | 105 | 17분 30초 | O |
| distraction | 15 | 2분 30초 | O |
| away | 9 | 1분 30초 | O |
| drowsy | 6 | 1분 | O |
| unknown | 15 | 2분 30초 | X |
| no_cam | 0 | 0초 | X |

판정 시간 = 1050 + 150 + 90 + 60 = 1350초 (전체 1500초의 90%), 점수 = floor(100 × 1050 / 1350 + 0.5) = floor(78.28) = **78점**.

```python
def focus_score(durations: list[float], final_tags: list[FinalTag]) -> int | None:
    total_sec = sum(durations)
    judged_sec = sum(d for d, t in zip(durations, final_tags) if t in JUDGED)
    if judged_sec < MIN_JUDGED_SEC or judged_sec < MIN_JUDGED_RATIO * total_sec:
        return None  # 판정 데이터 부족
    focus_sec = sum(d for d, t in zip(durations, final_tags) if t == "focus")
    return math.floor(100 * focus_sec / judged_sec + 0.5)
```

## 7. 점수 모드와 폴백

점수 계산 전에 세션 단위로 웹캠과 LLM 창 분류가 쓸 만한지 판단해 **점수 모드**를 정하고, 모드에 따라 태그 규칙을 바꾼다 (3장 `raw_tag`의 `mode` 분기).

- **웹캠 사용 가능**: `face_present is not None`인 슬롯의 시간이 `total_sec`의 `CAM_USABLE_RATIO`(30%) 이상.
- **LLM 사용 가능**: 창 분류 호출이 성공 (`llm_call_failed == False`). 보낼 항목이 하나도 없어 호출하지 않은 경우도 성공으로 본다 (모든 슬롯이 `unknown`이 되어 판정 데이터 부족으로 처리된다).

| `score_mode` | 조건 | 태그 규칙 변경 | `score_note` |
| --- | --- | --- | --- |
| `full` | 웹캠 O, LLM O | 없음 (3장 그대로) | None |
| `cam_only` | 웹캠 O, LLM X | 얼굴 있고 졸지 않은 슬롯은 규칙상 딴짓이면 `distraction`, 아니면 `focus`. LLM 딴짓 판정 없음 | "웹캠 신호와 기본 딴짓 규칙만으로 계산된 점수입니다" |
| `window_only` | 웹캠 X, LLM O | 1~3순위(`no_cam`·`away`·`drowsy`) 건너뜀. 창 라벨(규칙+LLM)로만 `focus`/`distraction`/`unknown` | "카메라 없이 창 사용만으로 계산된 점수입니다" |
| `none` | 웹캠 X, LLM X | 점수 계산 안 함 (`focus_score = None`) | "카메라와 창 분류를 모두 쓸 수 없어 점수를 계산하지 않았습니다" |

- `cam_only`에서 미분류를 집중으로 보는 이유: LLM이 실패하면 규칙에 걸리지 않은 모든 창이 미분류가 되어 분자가 0이 되기 때문이다. 규칙상 딴짓은 로컬 판정이라 LLM 실패와 무관하게 남는다.
- `none`(웹캠 X, LLM X)에서는 규칙상 딴짓만 판정할 수 있고 집중은 판정할 수 없어 점수가 항상 0이 되므로 계산하지 않는다.
- `full` 모드에서도 일부 슬롯만 카메라가 끊기면 그 슬롯은 `no_cam`으로 분모에서 빠진다. 리포트에는 "카메라 미인식 n분"으로 표시한다.
- 모드가 `full`·`cam_only`·`window_only`여도 6장의 판정 데이터 부족에 걸리면 `focus_score = None`, `score_note = "판정 데이터 부족"`이다. 모드 안내 문구보다 이 문구가 우선한다.

## 8. 출력 스키마와 리포트 표시

점수 모듈(신명철)은 기존 `SessionResult`를 채워 반환하고, GUI(김민서)는 이를 렌더링하고 로컬 저장한다. 세션 메타 필드(`session_id`, `session_start`, `session_end`, `ear_baseline`, `pause_intervals`, `report_text`, `llm_call_failed`)는 그대로 두고, 점수 관련 필드만 바꾼다. 저장 필드가 바뀌므로 `SCHEMA_VERSION`을 4로 올린다.

```python
ScoreMode = Literal["full", "cam_only", "window_only", "none"]
FinalTag = Literal["away", "drowsy", "distraction", "focus", "unknown", "no_cam"]  # no_cam 추가
RuleDistraction = Literal["foreground", "background"]  # 추가 (2.1)

class WindowSlot(TypedDict):         # 창 스레드 → GUI. MergedSlot에도 같은 필드 추가
    ...                              # 기존 필드 그대로 (app_name 등의 의미만 2.1 대표 창으로 바뀜)
    rule_distraction: RuleDistraction | None  # 추가. 창 메시지가 오지 않은 슬롯은 None

class SlotScore(TypedDict):
    slot: int
    start: float
    end: float                       # 마지막 슬롯은 세션 종료 시각
    app_name: str | None             # 추가: 리포트 타임라인·딴짓 집계용
    domain: str | None               # 추가
    video_category: str | None       # 추가
    rule_distraction: RuleDistraction | None  # 추가: 규칙 판정 여부 (검증·리포트용)
    window_label: WindowLabel
    raw_tag: FinalTag                # 추가: 3장 우선순위 태그
    final_tag: FinalTag              # 점수·리포트 기준. 유예 보정 폐기로 raw_tag와 같음 (4장)
    blink_count: int                 # 표시용
    # score: int                     # 삭제

class Distraction(TypedDict):        # 추가
    app_name: str | None             # 백그라운드 항목은 None
    domain: str | None               # 백그라운드 항목은 None
    video_category: str | None       # 백그라운드 항목은 background_category
    background: bool                 # rule_distraction == "background"에서 온 항목
    seconds: int

class SessionResult(TypedDict):
    schema_version: int              # 4
    session_id: str
    session_start: float
    session_end: float
    ear_baseline: float
    pause_intervals: list[PauseInterval]
    slot_scores: list[SlotScore]     # 정지 구간과 겹친 슬롯은 빠짐 (기존 그대로)
    score_mode: ScoreMode            # 추가
    focus_score: int | None          # 추가: 0~100, 계산 불가 시 None
    score_note: str | None           # 추가: 폴백·데이터 부족 안내
    tag_seconds: dict[str, int]      # 추가: final_tag별 누적 초 (FINAL_TAGS 6개 키 모두, 없으면 0)
    judged_seconds: int              # 추가: 분모에 들어간 시간
    top_distractions: list[Distraction]  # 추가: final_tag == distraction 시간 상위 TOP_DISTRACTIONS_N개
    longest_focus_seconds: int       # 추가: slot 번호가 이어지는 final_tag == focus 구간의 최장 시간
    blinks_per_minute: float | None  # 추가: 참고 지표, 점수 미반영
    report_text: str
    llm_call_failed: bool
    # 삭제: score_total, blink_bonus_total, drowsy_count, away_count, distraction_count
```

- `tag_seconds`, `judged_seconds`, `top_distractions[].seconds`는 초 합을 반올림한 정수다. 점수는 반올림 전 실수 합으로 계산한다.
- `top_distractions`: `final_tag == "distraction"`인 슬롯을 `(app_name, domain, video_category, background)`로 묶어 시간을 합한다. 백그라운드 슬롯은 앞 화면 창이 아니라 `(None, None, background_category, True)`로 묶는다 (앞 화면은 작업이었으므로).
- `longest_focus_seconds`: 일시정지로 slot 번호가 끊기면 연속 구간도 끊긴다.
- `blinks_per_minute` = (`face_present is True`인 슬롯의 `blink_count` 합) ÷ (그 슬롯들의 시간 / 60). 얼굴이 있던 시간이 `BLINK_RATE_MIN_SEC`(60초) 미만이면 None. 자리를 비운 시간을 분모에 넣으면 깜빡임이 적게 나오기 때문이다.
- 기존 v3 파일(`score_total` 등)은 리포트 목록에서 읽을 때 `schema_version`으로 구분한다. 처리 방법은 저장 담당(김민서)이 정한다.

리포트 화면 구성 (예시):

```
이번 세션 집중도  78점
판정된 22분 30초 기준                    ← 항상 표시
(score_note가 있으면 여기에)

집중       17분 30초  ████████████████░░░░
딴짓        2분 30초  (youtube.com [Gaming], 백그라운드 재생 [Entertainment])
자리비움    1분 30초
졸음        1분
판정 제외   2분 30초  (미분류 2분 30초 · 카메라 미인식 0분)

최장 연속 집중: 9분 10초
분당 깜빡임: 14.2회 (참고)
```

- 점수 옆에 등급(A/B/C)은 붙이지 않는다. 기준선을 정할 근거가 없기 때문이다.
- `focus_score`가 None이면 점수 자리에 `score_note`를 크게 표시하고, 태그별 시간은 그대로 보여준다.
- 태그 이름은 `schemas.FINAL_TAG_KO`로 바꿔 표시한다.

## 9. 설정값과 검증 계획

모두 **임시값**이다. 코드에 하드코딩하지 않는다. CV 신호·슬롯 압축 값은 기존 `config/aggregation.py`에, 딴짓 규칙 목록은 새 `config/window_rules.py`에, 점수 값은 `config/scoring_weights.py`를 대체하는 `config/scoring.py`에 둔다. 같은 값을 두 파일에 정의하지 않는다.

| 설정 | 임시값 | 파일 | 쓰이는 곳 | 튜닝 담당 |
| --- | --- | --- | --- | --- |
| `SLOT_SECONDS` | 10 | `schemas.py` (기존) | 전체 | 고정 |
| `FACE_PRESENT_MIN_RATIO` | 0.5 | `aggregation.py` (기존) | 2장 자리비움 | 김시언 |
| `CLOSURE_THRESHOLD` | 0.4 | `aggregation.py` (기존) | 2장 졸음 | 김시언 |
| `DROWSY_MIN_RUN_SEC` | 4 | `aggregation.py` (기존) | 2장 졸음 | 김시언 |
| `DISTRACTION_MIN_SEC` | 3 | `aggregation.py` (신규) | 2.1 앞 화면 규칙상 딴짓 | 신명철 |
| `BG_DISTRACTION_MIN_SEC` | 5 | `aggregation.py` (신규) | 2.1 백그라운드 재생 딴짓 | 신명철 |
| `DISTRACTION_VIDEO_CATEGORIES` | Gaming, Entertainment, Comedy, Film & Animation, Sports, People & Blogs (Music 제외) | `window_rules.py` (신규) | 2.1 | 신명철 |
| `DISTRACTION_DOMAINS` | 팀이 정함 | `window_rules.py` (신규) | 2.1 | 신명철 |
| `CAM_USABLE_RATIO` | 0.3 | `scoring.py` (신규) | 7장 웹캠 사용 가능 | 신명철 |
| `MIN_JUDGED_RATIO` | 0.3 | `scoring.py` (신규) | 6장 판정 데이터 부족 | 신명철 |
| `MIN_JUDGED_SEC` | 60 | `scoring.py` (신규) | 6장 판정 데이터 부족 | 신명철 |
| `TOP_DISTRACTIONS_N` | 3 | `scoring.py` (신규) | 8장 | 신명철 |
| `BLINK_RATE_MIN_SEC` | 60 | `scoring.py` (신규) | 8장 | 신명철 |
| LLM 타임아웃·재시도 | 미정 | `scoring.py` 또는 LLM 설정 | 5장 | 신명철 |

검증과 튜닝 순서:

1. **CV 신호 정확도** (김시언): 팀원이 집중·졸음·자리비움 상태를 의도적으로 재현하며 시각을 기록하고, 슬롯별 `face_present`·`drowsy`와 비교해 정밀도/재현율을 낸다. 이때 연속 감김 방식과 PERCLOS 방식(1초 원본으로 오프라인 계산)을 같은 데이터로 비교한다. 카메라 부분 끊김이 오탐을 내는지도 본다.
2. **창 분류 정확도** (신명철): 작업용·딴짓용 앱/도메인을 섞은 목록을 사람이 라벨링하고 LLM 분류 결과와 비교한다. 규칙상 딴짓은 "알트탭으로 잠깐 지나간 창"이 딴짓으로 잡히는지 보고 `DISTRACTION_MIN_SEC`를, 음악·강의를 틀어 둔 공부 세션이 딴짓으로 잡히지 않는지 보고 카테고리 목록과 `BG_DISTRACTION_MIN_SEC`를 조정한다.
3. **점수 타당성** (팀 공동): 참가자가 세션 직후 "이번 세션에 몇 % 집중했는지"를 스스로 적고, `focus_score`와의 상관관계를 본다. 짧은 이탈이 점수를 지나치게 깎는다면 유예 보정 재도입을 검토한다 (4장). 미분류 비율이 높은 세션에서 자기 평가와 차이가 큰지도 본다.

## 10. 확정 시 바꿀 파일

| 파일 | 변경 | 담당 |
| --- | --- | --- |
| `schemas.py` | `FinalTag`에 `no_cam`, `FINAL_TAG_KO`에 `"no_cam": "판정불가"`, `ScoreMode`·`RuleDistraction` 추가 / `WindowSlot`·`MergedSlot`에 `rule_distraction`, `empty_window_part()` 기본값 None / `SlotScore`·`SessionResult` 8장대로 / `LlmSlotInput`·`SlotLabel`·`LlmResponse`를 5장 항목 단위로 교체 / `final_tag()`를 3장 `raw_tag()`로 교체 / `SCHEMA_VERSION = 4` | 팀 합의 후 1명 |
| `config/scoring_weights.py` → `config/scoring.py` | 가중치 삭제, 9장 신규 값 | 신명철 |
| `config/aggregation.py` | `DISTRACTION_MIN_SEC`, `BG_DISTRACTION_MIN_SEC` 추가 | 신명철 |
| `config/window_rules.py` (신규) | `DISTRACTION_VIDEO_CATEGORIES`, `DISTRACTION_DOMAINS` | 신명철 |
| `window_tracker/tracker.py` | `build_window_slot()`을 2.1 우선순위 압축으로, `rule_distraction` 전송 | 신명철 |
| `main.py`, README | 유튜브 카테고리 기본 켬, 끄는 옵션 추가, 영상 ID 전송 안내 | 김민서 (통합) |
| `docs/FocusPomo_데이터_스키마_명세_v2.md` | WindowSlot·MergedSlot·LLM·SlotScore·SessionResult 절, 변경 이력 표 | schemas.py와 같은 PR |
| `docs/캡스톤_개발계획서.md` | 2.2절 압축 규칙·트레이드오프 문단·실행 기본값, 8장 결정 표(v9 "`--youtube`로만"), 2.3~2.5절을 이 문서 내용으로 | 김민서 |
| 점수 모듈 | 3·6·7·8장 구현 | 김민서 (SCORE-01, 신명철에게서 넘겨받음) |
| `core/slot_log.py` | 창 메시지의 `rule_distraction`을 MergedSlot에 복사 | 김민서 |
| 리포트 화면·`storage/` | 8장 표시, v3/v4 파일 구분 | 김민서 |

CV 스레드(`cv_module/`)의 출력은 바뀌지 않는다. 창 스레드(`window_tracker/`)는 필드 하나가 추가되고 대표 창을 고르는 규칙이 바뀐다.

## 11. 팀이 정해야 할 것

1. 졸음 판정을 기존 연속 감김 방식으로 유지하는 것에 동의하는지 (PERCLOS는 검증 후 재논의).
2. 판정불가 태그 이름 `no_cam`.
3. `DISTRACTION_DOMAINS` 목록 (숏폼·SNS·OTT 등).
4. 분류 호출과 리포트 문구 생성을 한 번에 할지 나눌지 (신명철).

**결정됨 (2026-11-09)**

- 유튜브 카테고리 조회는 기본으로 켠다 (2.1).
- 딴짓 카테고리에 `"People & Blogs"`를 넣고 `"Music"`은 넣지 않는다 (2.1).
- 유예 보정은 하지 않는다. `final_tag = raw_tag` (4장). 규칙상 딴짓 포함 여부 질문도 함께 없어짐.
