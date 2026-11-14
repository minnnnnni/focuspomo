"""명확한 딴짓 로컬 규칙 (점수 명세 2.1).

창 스레드가 슬롯을 압축할 때 이 목록에 걸리는 창(앞 화면)·영상(백그라운드 재생)을 바로 딴짓으로 표시한다.
목록에 없는 창은 세션 종료 후 LLM이 분류한다. 모두 임시값이며 9장 검증 후 튜닝한다.
사람이 읽는 표는 docs/딴짓_사이트_목록.md에 있다. 목록을 바꾸면 그 문서도 같은 PR에서 함께 고친다
(tests/test_window_rules.py가 두 목록이 같은지 확인한다).

- 카테고리 이름은 window_tracker/youtube_probe.py CATEGORY_NAMES의 영문 이름과 같아야 한다.
- Music·Education·Science & Technology는 공부하며 틀어 두는 경우가 많아 넣지 않는다 (2026-11-09 결정).
"""

DISTRACTION_VIDEO_CATEGORIES: frozenset[str] = frozenset({
    "Gaming",
    "Entertainment",
    "Comedy",
    "Film & Animation",
    "Sports",
    "People & Blogs",
})

# ── 딴짓 사이트 (브라우저 도메인) ────────────────────────────────────────────
# 형식: schemas.WindowSlot.domain과 같은 소문자 호스트, "www." 없이 (예: "instagram.com").
# 적은 도메인과 그 하위 도메인이 모두 걸린다: "instagram.com" → "m.instagram.com"도 딴짓.
# 그래서 공부에도 쓰는 큰 포털은 딴짓인 하위 도메인만 적는다 (예: naver.com 전체가 아니라 comic.naver.com).
#
# 넣는 기준: 집중 세션 중에 열려 있으면 거의 항상 공부와 무관한 사이트. 애매하면 넣지 않고 LLM에 맡긴다.
# 일부러 넣지 않은 것 (LLM이 분류):
#   youtube.com          강의도 많음. 영상 카테고리 규칙(DISTRACTION_VIDEO_CATEGORIES)으로 따로 판정
#   reddit.com           개발 질문 검색 결과로 자주 열림
#   linkedin.com         구직·업무
#   discord.com, messenger.com, web.whatsapp.com, band.us   팀·수업 소통에 씀
#   blog.naver.com, cafe.naver.com, tistory.com, velog.io, namu.wiki   공부 자료일 수 있음
#   naver.com, daum.net, 뉴스 사이트   검색·자료 조사
#   spotify.com, melon.com   공부하며 음악 듣기 (Music 카테고리와 같은 이유)
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
