"""브라우저 주소창 읽기·도메인 추출 — 1주차 스파이크용 초안 (Windows 실기 테스트 전).

pywinauto UIA 백엔드로 브라우저 창의 첫 번째 Edit 컨트롤(주소창) 값을 읽는다.
Chrome·Edge·Whale(모두 Chromium 계열)은 같은 방식으로 읽힐 것으로 예상, Firefox는 따로 확인 필요.
어떤 이유로든 실패하면 None을 돌려준다. 주소창 전체 텍스트는 메모리에서만 쓰고 저장하지 않는다.

주의: UIA는 COM을 쓰므로 호출하는 스레드에서 CoInitializeEx가 되어 있어야 한다
(WindowTracker.run에서 처리). pywinauto는 실제로 쓸 때만 import한다.
"""
from urllib.parse import urlsplit


def url_to_domain(text: str | None) -> str | None:
    """주소창 텍스트 → 도메인. "www.youtube.com/watch?v=..." → "youtube.com"."""
    if not text:
        return None
    text = text.strip()
    if not text or " " in text:  # 검색어를 입력하는 중
        return None
    if "://" not in text:  # Chrome은 기본적으로 https://를 숨김
        text = "https://" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return None
    if not host or "." not in host:  # chrome://newtab, localhost, 단어 하나짜리 검색어 등
        return None
    return host.removeprefix("www.")


def read_address_bar(hwnd: int) -> str | None:
    """브라우저 창의 주소창 텍스트. 못 읽으면 None."""
    try:
        from pywinauto import Application

        app = Application(backend="uia").connect(handle=hwnd, timeout=1)
        window = app.window(handle=hwnd)
        edits = window.descendants(control_type="Edit")
        if not edits:
            return None
        value = edits[0].iface_value.CurrentValue
    except Exception:
        return None
    return value or None


def read_browser_domain(hwnd: int) -> str | None:
    return url_to_domain(read_address_bar(hwnd))
