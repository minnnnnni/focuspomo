"""로컬 창 규칙 (점수 명세 2.1): 유튜브 영상 분류표와 딴짓 사이트 목록.

창 스레드가 1초마다 이 규칙으로 앞 화면 창·백그라운드 재생을 판정하고, 10초 슬롯으로 압축할 때
딴짓이면 rule_distraction을 붙인다. 유튜브 영상은 LLM에 보내지 않고 이 표로만 분류한다 (2026-11-25).
목록에 없는 창(유튜브가 아닌 사이트·앱)은 세션 종료 후 LLM이 분류한다. 모두 임시값이며 9장 검증 후 튜닝한다.

사람이 읽는 표: 유튜브는 docs/유튜브_영상_분류표.md, 사이트는 docs/딴짓_사이트_목록.md.
표를 바꾸면 그 문서도 같은 PR에서 함께 고친다 (tests/test_window_rules.py가 코드와 문서가 같은지 확인한다).
판정 로직은 window_tracker/video_rules.py, 이어 보기 리셋 시간 등 초 단위 값은 config/aggregation.py에 있다.
"""
from __future__ import annotations

from dataclasses import dataclass

from schemas import WindowLabel

# ── 유튜브 영상 분류표 ───────────────────────────────────────────────────────
# 영상 분류(video class) = 아래 표의 키. 정하는 순서 (window_tracker.video_rules.classify_video):
#   1. 주소가 /shorts/ → SHORTS (카테고리와 상관없이)
#   2. 제목이 공부 배경음 (BGM_TITLE_PATTERNS) → BGM (카테고리와 상관없이 Music처럼)
#   3. 그 외 → YouTube 카테고리 이름 (window_tracker/youtube_probe.py CATEGORY_NAMES의 영문 이름)
# WindowSlot.video_category·background_category에는 이 영상 분류가 들어간다.
SHORTS = "Shorts"
BGM = "BGM"

SETUP_GRACE_SEC = 60  # 노래·배경음을 고르는 시간 (검색 결과 훑기, 광고 건너뛰기). 이만큼은 앞 화면에 있어도 딴짓 아님
SETUP_FOCUS_SEC = 30  # 유예가 있는 분류를 이어서 본 묶음이 이만큼 이하로 끝나면 고르고 나간 것으로 보고 집중


@dataclass(frozen=True)
class VideoPolicy:
    """영상 분류 하나의 판정 규칙.

    foreground: 앞 화면에 띄워 두고 있을 때의 라벨.
    grace_sec: foreground가 "distraction"일 때의 고르는 시간. 0이면 바로 딴짓 (슬롯 안 DISTRACTION_MIN_SEC 기준은
        그대로). 0보다 크면 같은 분류를 앞 화면에서 이어서 본 묶음 하나의 전체 길이로 세션 종료 후 정한다 (소급):
        grace_sec 초과 → 그 묶음 전체 딴짓, SETUP_FOCUS_SEC 이하 → 집중 (고르고 나감), 그 사이 → 미분류.
        세션 중에도 grace_sec를 넘긴 뒤의 초는 창 스레드가 바로 규칙상 딴짓으로 표시한다.
    background_distraction: 앞 화면에 없이 재생만 될 때 딴짓인지. False면 무시하고 앞 화면 창으로 판정한다.
    """
    foreground: WindowLabel
    grace_sec: int = 0
    background_distraction: bool = False


# 원칙: 틀어 두기를 허용하는(background_distraction=False) 오락성 분류는 틀기 전에 고르는 시간이 있으므로 grace_sec를 둔다.
#       보든 틀어 두든 딴짓인 분류는 grace_sec 없이 바로 딴짓이다.
VIDEO_POLICIES: dict[str, VideoPolicy] = {
    # 공부 영상 — 앞 화면이면 작업, 틀어 두면 무시 (앞 화면 창으로 판정)
    "Education": VideoPolicy("work"),
    "Science & Technology": VideoPolicy("work"),
    # 섞여 있어 판단 보류 — 앞 화면이어도 미분류 (점수 분모에서 빠짐)
    "Howto & Style": VideoPolicy("unknown"),          # 뷰티·요리·DIY가 대부분, 코딩 강의는 보통 Education·과학기술
    "Nonprofits & Activism": VideoPolicy("unknown"),
    # 들으며 공부해도 되지만 계속 보고 있으면 딴짓
    "Music": VideoPolicy("distraction", grace_sec=SETUP_GRACE_SEC),
    BGM: VideoPolicy("distraction", grace_sec=SETUP_GRACE_SEC),
    "News & Politics": VideoPolicy("distraction", grace_sec=180),  # 과제 자료로 뉴스 클립 하나는 볼 수 있음
    "Travel & Events": VideoPolicy("distraction", grace_sec=SETUP_GRACE_SEC),  # 빗소리·카페 소음 채널이 많이 고름
    "Pets & Animals": VideoPolicy("distraction", grace_sec=SETUP_GRACE_SEC),   # 동물 소리 배경음
    # 보든 틀어 두든 딴짓
    "Gaming": VideoPolicy("distraction", background_distraction=True),
    "Entertainment": VideoPolicy("distraction", background_distraction=True),
    "Comedy": VideoPolicy("distraction", background_distraction=True),
    "Film & Animation": VideoPolicy("distraction", background_distraction=True),
    "Sports": VideoPolicy("distraction", background_distraction=True),
    "People & Blogs": VideoPolicy("distraction", background_distraction=True),  # 브이로그. 플리·백색소음은 BGM으로 빠짐
    "Autos & Vehicles": VideoPolicy("distraction", background_distraction=True),
    SHORTS: VideoPolicy("distraction", background_distraction=True),  # 숏폼은 틱톡과 같게
}

# 표에 없는 분류 (예: "Unknown(44)" — API가 준 카테고리 ID가 CATEGORY_NAMES에 없음)
DEFAULT_VIDEO_POLICY = VideoPolicy("unknown")

# 공부 배경음 제목 (정규식, 대소문자 무시, 하나라도 들어 있으면 BGM).
# 플리·백색소음 채널은 카테고리를 People & Blogs·Entertainment로 고르는 경우가 많아, 카테고리만 보면
# 틀어 두기만 해도 딴짓이 된다. 강의 제목에 흔한 말("1시간", "피아노", "jazz" 등)은 넣지 않는다.
# 제목은 판정에만 쓰고 저장하거나 내보내지 않는다.
BGM_TITLE_PATTERNS: tuple[str, ...] = (
    r"\bplay\s?list",
    r"플레이\s?리스트",
    r"플리(?![가-힣])",        # "플리", "[플리]" — "플리즈", "플리마켓"은 제외
    r"lo-?fi",
    r"asmr",
    r"백색\s?소음",
    r"white\s?noise",
    r"study\s?with\s?me",
    r"스터디\s?윗\s?미",
    r"공부할\s?때",
    r"공부하면서",
    r"작업할\s?때",
    r"(공부|집중|작업|카페)\s?음악",
    r"cafe\s?music",
    r"노동요",
    r"빗소리",
    r"rain\s?sounds?",
    r"ambience",
    r"bgm",
)

# ── 딴짓 사이트 (브라우저 도메인) ────────────────────────────────────────────
# 형식: schemas.WindowSlot.domain과 같은 소문자 호스트, "www." 없이 (예: "instagram.com").
# 적은 도메인과 그 하위 도메인이 모두 걸린다: "instagram.com" → "m.instagram.com"도 딴짓.
# 그래서 공부에도 쓰는 큰 포털은 딴짓인 하위 도메인만 적는다 (예: naver.com 전체가 아니라 comic.naver.com).
#
# 넣는 기준: 집중 세션 중에 열려 있으면 거의 항상 공부와 무관한 사이트. 애매하면 넣지 않고 LLM에 맡긴다.
# 일부러 넣지 않은 것 (LLM이 분류):
#   youtube.com          강의도 많음. 영상은 위 유튜브 영상 분류표로 따로 판정 (홈·검색 화면은 LLM → 미분류)
#   reddit.com           개발 질문 검색 결과로 자주 열림
#   linkedin.com         구직·업무
#   discord.com, messenger.com, web.whatsapp.com, band.us   팀·수업 소통에 씀
#   blog.naver.com, cafe.naver.com, tistory.com, velog.io, namu.wiki   공부 자료일 수 있음
#   naver.com, daum.net, 뉴스 사이트   검색·자료 조사
#   spotify.com, melon.com   공부하며 음악 듣기 (Music 영상을 틀어 두기 허용한 것과 같은 이유)
#   ridibooks.com        전공 전자책
#   amazon.com           aws.amazon.com(AWS 콘솔)까지 걸려서 뺌
#   clien.net            IT 정보 게시판이 섞여 있음
DISTRACTION_DOMAIN_GROUPS: dict[str, frozenset[str]] = {
    # SNS
    "sns": frozenset({
        "instagram.com",
        "facebook.com",
        "x.com",
        "twitter.com",
        "threads.net",
        "threads.com",
        "bsky.app",
        "tumblr.com",
        "pinterest.com",
        "snapchat.com",
    }),
    # 숏폼·OTT (영상 스트리밍)
    "video": frozenset({
        "tiktok.com",
        "netflix.com",
        "tving.com",
        "wavve.com",
        "coupangplay.com",
        "watcha.com",
        "disneyplus.com",
        "primevideo.com",
        "laftel.net",
    }),
    # 인터넷 방송
    "live": frozenset({
        "twitch.tv",
        "chzzk.naver.com",
        "sooplive.co.kr",
        "afreecatv.com",
        "kick.com",
    }),
    # 커뮤니티 (잡담·유머·연예·스포츠)
    "community": frozenset({
        "dcinside.com",
        "fmkorea.com",
        "theqoo.net",
        "instiz.net",
        "pann.nate.com",
        "arca.live",
        "ruliweb.com",
        "todayhumor.co.kr",
        "humoruniv.com",
        "mlbpark.donga.com",
        "bobaedream.co.kr",
    }),
    # 웹툰·웹소설
    "webtoon": frozenset({
        "comic.naver.com",
        "series.naver.com",
        "webtoons.com",
        "webtoon.kakao.com",
        "page.kakao.com",
        "lezhin.com",
        "munpia.com",
        "novelpia.com",
    }),
    # 게임 (웹 게임·게임 스토어·전적 검색)
    "game": frozenset({
        "store.steampowered.com",
        "steamcommunity.com",
        "store.epicgames.com",
        "inven.co.kr",
        "op.gg",
        "leagueoflegends.com",
        "nexon.com",
        "roblox.com",
        "poki.com",
        "crazygames.com",
        "chess.com",
        "lichess.org",
    }),
    # 쇼핑
    "shopping": frozenset({
        "coupang.com",
        "11st.co.kr",
        "gmarket.co.kr",
        "auction.co.kr",
        "ssg.com",
        "shopping.naver.com",
        "smartstore.naver.com",
        "musinsa.com",
        "29cm.co.kr",
        "kream.co.kr",
        "zigzag.kr",
        "a-bly.com",
        "ohou.se",
        "aliexpress.com",
        "temu.com",
        "danawa.com",
        "ppomppu.co.kr",
    }),
}

# 화면·문서용 그룹 이름
DISTRACTION_DOMAIN_GROUP_KO: dict[str, str] = {
    "sns": "SNS",
    "video": "숏폼·OTT",
    "live": "인터넷 방송",
    "community": "커뮤니티",
    "webtoon": "웹툰·웹소설",
    "game": "게임",
    "shopping": "쇼핑",
}

DISTRACTION_DOMAINS: frozenset[str] = frozenset().union(*DISTRACTION_DOMAIN_GROUPS.values())
