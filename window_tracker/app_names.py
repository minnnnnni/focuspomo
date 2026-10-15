"""실행 파일 이름(exe) → app_name 표시 이름 매핑 (임시값 — 팀 합의 후 확정).

스키마 명세 v1의 예시가 "VS Code", "Chrome" 같은 표시 이름이라 exe 이름을 매핑한다.
매핑에 없는 exe는 확장자만 떼서 그대로 쓴다 (예: "Obsidian.exe" → "Obsidian").
"""

APP_DISPLAY_NAMES: dict[str, str] = {
    # 개발
    "code.exe": "VS Code",
    "pycharm64.exe": "PyCharm",
    "devenv.exe": "Visual Studio",
    "windowsterminal.exe": "Windows Terminal",
    "powershell.exe": "PowerShell",
    "cmd.exe": "Command Prompt",
    # 브라우저
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "whale.exe": "Whale",
    # 문서·학습
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "hwp.exe": "HWP",
    "acrobat.exe": "Acrobat",
    "acrord32.exe": "Acrobat Reader",
    "notion.exe": "Notion",
    "explorer.exe": "File Explorer",
    "systemsettings.exe": "Settings",
    # 메신저·기타
    "kakaotalk.exe": "KakaoTalk",
    "discord.exe": "Discord",
    "slack.exe": "Slack",
    "zoom.exe": "Zoom",
    "spotify.exe": "Spotify",
}

BROWSER_EXES = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "whale.exe"})

# 포그라운드여도 "조회 실패"로 보고 app_name=None으로 보낼 프로세스 (실기 테스트로 보완 필요)
SYSTEM_EXES = frozenset({
    "lockapp.exe",                   # 잠금 화면
    "logonui.exe",                   # 로그인 화면
    "startmenuexperiencehost.exe",   # 시작 메뉴 (Win11)
    "shellexperiencehost.exe",       # 시작 메뉴·알림 센터 (Win10)
    "searchhost.exe",                # 검색 (Win11)
    "searchapp.exe",                 # 검색 (Win10)
})

# explorer.exe 중 파일 탐색기가 아닌 셸 화면 (클래스 이름은 Windows 버전마다 다를 수 있음)
SYSTEM_WINDOW_CLASSES = frozenset({
    "Progman", "WorkerW",            # 바탕화면
    "Shell_TrayWnd",                 # 작업 표시줄
    "MultitaskingViewFrame",         # Win+Tab / Alt+Tab (Win10)
    "XamlExplorerHostIslandWindow",  # Win+Tab / Alt+Tab (Win11)
    "ForegroundStaging",             # 창 전환 중 잠깐 잡히는 창
})


def display_name(exe: str) -> str:
    key = exe.lower()
    if key in APP_DISPLAY_NAMES:
        return APP_DISPLAY_NAMES[key]
    return exe[:-4] if key.endswith(".exe") else exe


def resolve_app_name(exe: str | None, window_class: str | None) -> str | None:
    """exe 이름과 창 클래스로 app_name을 정한다. 시스템 창·조회 실패면 None."""
    if not exe:
        return None
    if exe.lower() in SYSTEM_EXES:
        return None
    if window_class in SYSTEM_WINDOW_CLASSES:
        return None
    return display_name(exe)


def is_browser(exe: str | None) -> bool:
    return exe is not None and exe.lower() in BROWSER_EXES
