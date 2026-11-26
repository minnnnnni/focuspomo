"""고개 각도 측정 기록(CSV) 분석 — 필기 자세 판정 기준을 정하기 위한 숫자를 뽑는다.

측정은 cv_module/measure_head_pose.py로 하고, 끝나면 이 분석이 자동으로 돈다. 따로 돌리려면 (레포 루트에서):
    python -m cv_module.analyze_head_pose data/head_pose/head_pose_20261126_140000.csv

보는 것:
1. 상황별 요약 — 각도·눈 비율·얼굴 검출률, 그리고 '지금 앱 규칙'으로 판정하면 졸음·자리비움이 몇 초인지
   (cv_module.second_summary·drowsiness와 같은 규칙: 기준의 60% 미만 감김이 4초 이어지면 졸음, 1초 중 과반 프레임에서
   얼굴이 없으면 자리비움 쪽)
2. 숙인 각도 구간별 눈 비율 — 화면 보기·필기에서 몇 도부터 지금 규칙이 '감김'으로 잘못 보는지 (구간별 기준값 근거)
3. 숙이기 직전·직후 눈 비율 — 필기와 '졸다가 고개 떨굼'에서 숙이기 전에 눈 비율이 다른지 (아이디어 검증)
4. 얼굴을 놓친 순간 — 놓치기 직전 각도·얼굴 위치, 놓친 동안 사람 영역 비율 (숙임과 자리 이탈 구분 기준)

결과는 화면에 출력하고 CSV 옆에 <이름>_summary.txt로 저장한다. 이 파일(과 CSV)을 공유하면 된다. 영상·사진은 없다.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path
from typing import Iterable

from config.aggregation import CLOSURE_THRESHOLD, DROWSY_MIN_RUN_SEC
from cv_module.drowsiness import ClosedRunTracker, is_drowsy

LABELS: dict[str, str] = {           # 측정 스크립트의 키 1~6
    "screen": "화면 보기",
    "write": "필기",
    "nod": "졸다가 고개 떨굼",
    "doze_down": "숙인 채 졸기",
    "leave_down": "숙인 채 일어나 나감",
    "leave": "평소 자세에서 나감",
}
ONSET_PITCH = 20.0   # 숙이기 시작으로 볼 평소 대비 각도 (임시 — 이 분석 결과로 정한다)
PITCH_BIN = 5        # 각도 구간 폭 (도)
FLOAT_FIELDS = ("t", "ear", "ear_base", "ear_ratio", "pitch", "pitch_base", "pitch_rel", "yaw", "roll",
                "blink", "look_down", "look_up", "face_cx", "face_cy", "face_w", "person", "person_center")


# ── 읽기 ─────────────────────────────────────────────

def _num(value: str) -> float | None:
    return float(value) if value not in ("", "-", None) else None


def parse_row(raw: dict) -> dict:
    row = {k: _num(raw.get(k, "")) for k in FLOAT_FIELDS}
    row["label"] = raw.get("label", "") or ""
    row["face"] = raw.get("face") == "1"
    row["closed"] = raw.get("closed") == "1"
    return row


def load(path: Path) -> tuple[list[dict], dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = [parse_row(r) for r in csv.DictReader(f)]
    meta_path = path.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return rows, meta


# ── 통계 도우미 ──────────────────────────────────────

def median(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def percentile(values: Iterable[float | None], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def fmt(value: float | None, spec: str = ".2f", none: str = "-") -> str:
    return none if value is None else format(value, spec)


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


# ── 1. 지금 앱 규칙으로 판정 ─────────────────────────

def simulate_current_rule(rows: list[dict]) -> list[dict]:
    """프레임 기록 → 1초 판정 (지금 앱과 같은 규칙). 초마다 label은 그 초 프레임의 최빈 라벨."""
    buckets: dict[int, list[dict]] = {}
    for row in rows:
        buckets.setdefault(int(row["t"]), []).append(row)
    tracker = ClosedRunTracker()
    seconds = []
    for index in sorted(buckets):
        frames = buckets[index]
        face_frames = sum(r["face"] for r in frames)
        face_present = face_frames * 2 > len(frames)
        eyes_closed = face_present and all(r["closed"] for r in frames)
        run = tracker.update({"timestamp": float(index), "eyes_closed": eyes_closed})  # type: ignore[arg-type]
        labels = [r["label"] for r in frames]
        seconds.append({
            "index": index,
            "label": max(set(labels), key=labels.count),
            "face_present": face_present,
            "eyes_closed": eyes_closed,
            "closed_run": run,
            "drowsy": is_drowsy(run),
        })
    return seconds


def label_stats(rows: list[dict], seconds: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for label in LABELS:
        frames = [r for r in rows if r["label"] == label]
        if not frames:
            continue
        faced = [r for r in frames if r["face"]]
        missing = [r for r in frames if not r["face"]]
        secs = [s for s in seconds if s["label"] == label]
        stats[label] = {
            "seconds": len(secs),
            "face_rate": ratio(len(faced), len(frames)),
            "pitch_p10": percentile((r["pitch_rel"] for r in faced), 0.1),
            "pitch_p50": percentile((r["pitch_rel"] for r in faced), 0.5),
            "pitch_p90": percentile((r["pitch_rel"] for r in faced), 0.9),
            "ear_ratio": median(r["ear_ratio"] for r in faced),
            "closed_rate": ratio(sum(r["closed"] for r in faced), len(faced)),
            "blink": median(r["blink"] for r in faced),
            "look_down": median(r["look_down"] for r in faced),
            "person": median(r["person"] for r in frames),
            "person_missing": median(r["person"] for r in missing),
            "drowsy_sec": sum(s["drowsy"] for s in secs),
            "away_sec": sum(not s["face_present"] for s in secs),
        }
    return stats


# ── 2. 각도 구간별 눈 비율 ───────────────────────────

def pitch_bins(rows: list[dict], labels: tuple[str, ...] = ("screen", "write"), width: int = PITCH_BIN) -> list[dict]:
    """깨어 있는 상황(화면 보기·필기)의 얼굴 프레임을 평소 대비 각도 구간으로 묶는다."""
    groups: dict[int, list[dict]] = {}
    for r in rows:
        if r["label"] in labels and r["face"] and r["pitch_rel"] is not None:
            groups.setdefault(int(r["pitch_rel"] // width) * width, []).append(r)
    return [{
        "lo": lo,
        "frames": len(g),
        "ear_ratio": median(r["ear_ratio"] for r in g),
        "closed_rate": ratio(sum(r["closed"] for r in g), len(g)),
        "blink": median(r["blink"] for r in g),
        "look_down": median(r["look_down"] for r in g),
    } for lo, g in sorted(groups.items())]


# ── 3. 숙이기 직전·직후 ──────────────────────────────

def onset_events(rows: list[dict], threshold: float = ONSET_PITCH, min_below_sec: float = 1.0) -> list[dict]:
    """평소 대비 각도가 threshold를 아래에서 위로 넘는 순간 (그 전 min_below_sec 이상 아래였을 때만).

    직전 구간을 둘로 나눠 본다: 3~1초 전(고개 숙이기 전), 1초 전~0(눈이 먼저 내려가는 구간일 수 있음).
    """
    faced = [r for r in rows if r["face"] and r["pitch_rel"] is not None]
    events = []
    below_since: float | None = None
    prev_below = False
    for r in faced:
        below = r["pitch_rel"] < threshold
        if below:
            if not prev_below:
                below_since = r["t"]
        elif prev_below and below_since is not None and r["t"] - below_since >= min_below_sec:
            t0 = r["t"]

            def window(a: float, b: float, key: str) -> float | None:
                return median(x[key] for x in faced if t0 + a <= x["t"] < t0 + b)

            events.append({
                "t": t0,
                "label": r["label"],
                "ear_before": window(-3, -1, "ear_ratio"),
                "ear_last": window(-1, 0, "ear_ratio"),
                "ear_after": window(0, 2, "ear_ratio"),
                "blink_before": window(-3, -1, "blink"),
                "down_last": window(-1, 0, "look_down"),
            })
        prev_below = below
    return events


# ── 4. 얼굴을 놓친 순간 ──────────────────────────────

def face_loss_events(rows: list[dict], min_sec: float = 1.0) -> list[dict]:
    """얼굴을 min_sec 이상 놓친 구간. 놓치기 직전 프레임의 각도·위치와 놓친 동안 사람 영역 비율."""
    events = []
    last_face: dict | None = None
    lost: list[dict] = []

    def close() -> None:
        if lost and lost[-1]["t"] - lost[0]["t"] >= min_sec:
            labels = [r["label"] for r in lost]
            events.append({
                "t": lost[0]["t"],
                "duration": lost[-1]["t"] - lost[0]["t"],
                "label": max(set(labels), key=labels.count),
                "last_pitch": last_face["pitch_rel"] if last_face else None,
                "last_cx": last_face["face_cx"] if last_face else None,
                "last_cy": last_face["face_cy"] if last_face else None,
                "last_w": last_face["face_w"] if last_face else None,
                "person": median(r["person"] for r in lost),
                "person_center": median(r["person_center"] for r in lost),
            })

    for r in rows:
        if r["face"]:
            close()
            lost = []
            last_face = r
        else:
            lost.append(r)
    close()
    return events


# ── 출력 ─────────────────────────────────────────────

def sign_warning(stats: dict[str, dict]) -> str | None:
    write = stats.get("write")
    if write and write["pitch_p50"] is not None and write["pitch_p50"] < -5:
        return ("※ 필기 중 각도가 평소보다 '작게' 나왔습니다. 각도 부호가 반대일 가능성이 큽니다 — "
                "측정 스크립트를 --flip-pitch로 다시 돌리거나 측정 중 f 키로 뒤집어 주세요.")
    return None


def format_summary(path: Path, rows: list[dict], meta: dict) -> str:
    seconds = simulate_current_rule(rows)
    stats = label_stats(rows, seconds)
    lines = [f"# 고개 각도 측정 요약 — {path.name}", ""]
    calib = (meta.get("calibrations") or [{}])[-1]
    lines.append(f"기록 {len(rows)}프레임, {len(seconds)}초, 평균 {fmt(meta.get('fps_mean'), '.1f')}fps, "
                 f"눈 기준값 {fmt(calib.get('ear_baseline'), '.3f')}, 평소 각도 {fmt(calib.get('pitch_baseline'), '+.1f')}°, "
                 f"사람 영역 모델 {'켬' if meta.get('segmenter') else '끔'}, 각도 뒤집기 {'켬' if meta.get('flip_pitch') else '끔'}")
    lines.append(f"지금 규칙: 기준의 {100 - CLOSURE_THRESHOLD * 100:.0f}% 미만이면 감김, {DROWSY_MIN_RUN_SEC}초 이어지면 졸음 / "
                 "1초 중 과반 프레임에서 얼굴이 없으면 자리비움 쪽")
    warning = sign_warning(stats)
    if warning:
        lines += ["", warning]

    lines += ["", "## 1. 상황별 요약 (각도는 평소 대비, +가 숙임)", "",
              "| 상황 | 초 | 얼굴 검출 | 각도 p10 / p50 / p90 | 눈 비율 | 감김 프레임 | 눈감음 점수 | 시선아래 점수 "
              "| 사람 영역 (얼굴 없을 때) | 지금 규칙: 졸음 / 자리비움 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for label, s in stats.items():
        lines.append(
            f"| {LABELS[label]} | {s['seconds']} | {pct(s['face_rate'])} | "
            f"{fmt(s['pitch_p10'], '+.0f')} / {fmt(s['pitch_p50'], '+.0f')} / {fmt(s['pitch_p90'], '+.0f')} | "
            f"{fmt(s['ear_ratio'])} | {pct(s['closed_rate'])} | {fmt(s['blink'])} | {fmt(s['look_down'])} | "
            f"{fmt(s['person'])} ({fmt(s['person_missing'])}) | {s['drowsy_sec']}초 / {s['away_sec']}초 |")
    if not stats:
        lines.append("| (라벨이 붙은 기록이 없습니다 — 측정 중 1~6 키로 상황을 고르세요) |")

    lines += ["", "## 2. 각도 구간별 눈 비율 (화면 보기·필기, 깨어 있을 때)", "",
              "| 평소 대비 각도 | 프레임 | 눈 비율 (기준=1) | 지금 규칙상 감김 | 눈감음 점수 | 시선아래 점수 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for b in pitch_bins(rows):
        lines.append(f"| {b['lo']:+d}° ~ {b['lo'] + PITCH_BIN:+d}° | {b['frames']} | {fmt(b['ear_ratio'])} | "
                     f"{pct(b['closed_rate'])} | {fmt(b['blink'])} | {fmt(b['look_down'])} |")

    events = onset_events(rows)
    lines += ["", f"## 3. 숙이기 직전·직후 눈 비율 (평소 대비 {ONSET_PITCH:.0f}°를 넘는 순간, {len(events)}회)", "",
              "| 상황 | 횟수 | 3~1초 전 | 1초 전~0 | 0~2초 후 | 3~1초 전 눈감음 점수 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for label in LABELS:
        group = [e for e in events if e["label"] == label]
        if group:
            lines.append(f"| {LABELS[label]} | {len(group)} | {fmt(median(e['ear_before'] for e in group))} | "
                         f"{fmt(median(e['ear_last'] for e in group))} | {fmt(median(e['ear_after'] for e in group))} | "
                         f"{fmt(median(e['blink_before'] for e in group))} |")

    losses = face_loss_events(rows)
    lines += ["", f"## 4. 얼굴을 1초 넘게 놓친 구간 ({len(losses)}회)", "",
              "| 상황 | 시작(초) | 길이(초) | 직전 각도 | 직전 얼굴 위치 x / y / 폭 | 사람 영역 / 가운데 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for e in losses:
        lines.append(f"| {LABELS.get(e['label'], '전환 중')} | {e['t']:.0f} | {e['duration']:.1f} | "
                     f"{fmt(e['last_pitch'], '+.0f')} | {fmt(e['last_cx'])} / {fmt(e['last_cy'])} / {fmt(e['last_w'])} | "
                     f"{fmt(e['person'])} / {fmt(e['person_center'])} |")
    lines.append("")
    return "\n".join(lines)


def analyze(path: Path) -> Path:
    rows, meta = load(path)
    text = format_summary(path, rows, meta)
    out = path.with_name(path.stem + "_summary.txt")
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"요약 저장: {out}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="고개 각도 측정 기록 분석")
    parser.add_argument("paths", nargs="+", type=Path, help="measure_head_pose가 만든 CSV")
    for p in parser.parse_args().paths:
        analyze(p)
