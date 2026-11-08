"""명확한 딴짓 로컬 규칙 (점수 명세 2.1).

창 스레드가 슬롯을 압축할 때 이 목록에 걸리는 창(앞 화면)·영상(백그라운드 재생)을 바로 딴짓으로 표시한다.
목록에 없는 창은 세션 종료 후 LLM이 분류한다. 모두 임시값이며 9장 검증 후 튜닝한다.

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

# 브라우저 도메인 (schemas.WindowSlot.domain 형식, 예: "instagram.com"). 목록은 팀이 정한다 (명세 11장)
DISTRACTION_DOMAINS: frozenset[str] = frozenset()
