"""백그라운드 재생 감지 진단 — 같은 미디어 상태를 네 가지 방식으로 동시에 읽어 비교한다.

레포 최상위(main.py가 있는 폴더)에 저장하고 venv에서 실행:
    python media_diag.py

진행 (40초):
    시작 전: 유튜브 영상을 '일시정지' 상태로 두고, VS Code를 앞에 둔다
    10초쯤: 브라우저로 가서 영상 재생 → 바로 VS Code로 돌아온다
    25초쯤: 브라우저로 가서 일시정지 → 바로 VS Code로 돌아온다

정상이라면 네 열 모두 재생 중엔 True, 일시정지 중엔 False로 바뀐다.
처음 값에서 끝까지 안 바뀌는 열이 있으면, 그 방식은 상태 변화를 따라가지 못하는 것이다.
    A 메인 스레드, 처음 만든 프로브를 계속 사용    (지난번 한 줄 명령과 같은 방식)
    B 메인 스레드, 매번 새로 만든 프로브
    C 별도 스레드 + COM(MTA) 초기화, 계속 사용        (창 추적 스레드에서 미디어만 켠 경우)
    D C와 같고 주소창 읽기(pywinauto)도 같은 스레드에서 (통합 테스트에서 실제로 쓴 조합)
D 열 오른쪽에는 통합 테스트와 같은 계산으로 '앞 창'과 '백그라운드 여부'를 함께 찍는다.
영상 제목은 화면에만 잠깐 쓰고 저장하지 않는다.
"""
from __future__ import annotations

import threading
import time

from window_tracker.media_probe import MediaProbe

DURATION = 40
INTERVAL = 1.0
latest: dict[str, str] = {"C": "…", "D": "…", "D_extra": ""}
stop = threading.Event()


def fmt(state) -> str:
    return {True: "True ", False: "False", None: "None "}[state.playing]


def thread_probe(key: str, with_domain: bool) -> None:
    from window_tracker.tracker import _init_com, make_sample

    com = _init_com()
    sampler = None
    if with_domain:
        from window_tracker.foreground import ForegroundSampler
        sampler = ForegroundSampler(use_domain_probe=True)
    probe = MediaProbe()
    try:
        while not stop.is_set():
            state = probe.sample()
            latest[key] = fmt(state)
            if sampler is not None:
                fg = sampler.sample()
                s = make_sample(time.time(), fg, state)
                latest["D_extra"] = f"앞={fg.app_name or '-'}{'(' + fg.domain + ')' if fg.domain else ''} " \
                                    f"백그라운드={s['background_media']}"
            stop.wait(INTERVAL)
    except Exception as e:  # 진단용이라 어떤 오류든 화면에 보여준다
        latest[key] = f"오류 {type(e).__name__}: {e}"
    finally:
        probe.close()
        if com is not None:
            com.CoUninitialize()


def main() -> None:
    print("미디어 감지 진단 (40초)")
    print("시작 전: 영상 일시정지 + VS Code 앞에. 10초쯤 재생, 25초쯤 일시정지 (매번 바로 VS Code로 복귀)\n")
    threads = [threading.Thread(target=thread_probe, args=("C", False), daemon=True),
               threading.Thread(target=thread_probe, args=("D", True), daemon=True)]
    for t in threads:
        t.start()
    probe_a = MediaProbe()
    history: dict[str, list[tuple[int, str]]] = {k: [] for k in "ABCD"}
    print("  초 | A 메인·유지 | B 메인·매번 | C 스레드+COM | D +주소창 | D 기준 앞 창·백그라운드 | 앱")
    start = time.time()
    try:
        while (t := time.time() - start) < DURATION:
            a = probe_a.sample()
            probe_b = MediaProbe()
            b = probe_b.sample()
            probe_b.close()
            row = {"A": fmt(a), "B": fmt(b), "C": latest["C"], "D": latest["D"]}
            for k, v in row.items():
                if not history[k] or history[k][-1][1] != v:
                    history[k].append((int(t), v))
            print(f"{t:4.0f} | {row['A']:^11} | {row['B']:^11} | {row['C']:^12} | {row['D']:^9} | "
                  f"{latest['D_extra']:24} | {a.app or b.app or '-'}", flush=True)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        probe_a.close()
        for t in threads:
            t.join(timeout=2)
    print("\n값이 바뀐 시각 (초: 값)")
    for k, name in zip("ABCD", ("A 메인·유지", "B 메인·매번", "C 스레드+COM", "D +주소창")):
        print(f"  {name:12} " + ", ".join(f"{s}초: {v.strip()}" for s, v in history[k]))


if __name__ == "__main__":
    main()
