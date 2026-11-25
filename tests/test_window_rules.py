"""config/window_rules.py 로컬 규칙 검사 (WIN-04·05, 점수 명세 2.1): 딴짓 사이트 목록, 유튜브 영상 분류표."""
import re
import unittest
from pathlib import Path

from config.window_rules import (
    BGM,
    BGM_TITLE_PATTERNS,
    DEFAULT_VIDEO_POLICY,
    DISTRACTION_DOMAIN_GROUP_KO,
    DISTRACTION_DOMAIN_GROUPS,
    DISTRACTION_DOMAINS,
    SHORTS,
    VIDEO_POLICIES,
    VideoPolicy,
)
from schemas import WINDOW_LABELS
from window_tracker.domain_probe import url_to_domain
from window_tracker.youtube_probe import CATEGORY_NAMES

DOCS = Path(__file__).resolve().parents[1] / "docs"
DOC = DOCS / "딴짓_사이트_목록.md"
VIDEO_DOC = DOCS / "유튜브_영상_분류표.md"
LABEL_KO = {"작업": "work", "미분류": "unknown", "딴짓": "distraction"}
BACKGROUND_KO = {"딴짓": True, "무시": False}


def doc_section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def doc_domains() -> set[str]:
    """문서의 '## 딴짓 사이트' 표에서 도메인 열(백틱)만 읽는다."""
    section = doc_section(DOC, "딴짓 사이트")
    return {m for line in section.splitlines() if line.startswith("|")
            for m in re.findall(r"`([^`]+)`", line)}


def doc_video_policies() -> dict[str, VideoPolicy]:
    """유튜브 영상 분류표 문서의 '## 분류표' 표 → {영상 분류: VideoPolicy}."""
    policies = {}
    for line in doc_section(VIDEO_DOC, "분류표").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        name = cells[0].strip("`")
        grace = 0 if cells[2] == "-" else int(cells[2].removesuffix("초"))
        policies[name] = VideoPolicy(LABEL_KO[cells[1]], grace, BACKGROUND_KO[cells[3]])
    return policies


class DomainListTest(unittest.TestCase):
    def test_same_form_as_tracker_domain(self):
        """주소창에서 뽑는 domain과 같은 형식이어야 걸린다: 소문자, www·스킴·경로 없음."""
        for domain in DISTRACTION_DOMAINS:
            with self.subTest(domain=domain):
                self.assertEqual(url_to_domain(domain), domain)
                self.assertEqual(domain, domain.lower())
                self.assertFalse(domain.startswith("www."))
                self.assertIn(".", domain)

    def test_no_domain_in_two_groups(self):
        seen: dict[str, str] = {}
        for group, domains in DISTRACTION_DOMAIN_GROUPS.items():
            for domain in domains:
                self.assertNotIn(domain, seen, f"{domain}: {seen.get(domain)}, {group}")
                seen[domain] = group

    def test_every_group_has_korean_name(self):
        self.assertEqual(set(DISTRACTION_DOMAIN_GROUP_KO), set(DISTRACTION_DOMAIN_GROUPS))

    def test_union(self):
        self.assertEqual(DISTRACTION_DOMAINS, frozenset().union(*DISTRACTION_DOMAIN_GROUPS.values()))

    def test_requested_sns(self):
        """인스타·페이스북·X는 반드시 딴짓."""
        for domain in ("instagram.com", "facebook.com", "x.com", "twitter.com"):
            self.assertIn(domain, DISTRACTION_DOMAIN_GROUPS["sns"])

    def test_study_sites_are_not_listed(self):
        """공부·팀 작업에 쓰는 사이트는 규칙에 넣지 않는다 (LLM이 분류)."""
        for domain in ("youtube.com", "naver.com", "github.com", "stackoverflow.com", "discord.com",
                       "reddit.com", "amazon.com", "notion.so"):
            self.assertNotIn(domain, DISTRACTION_DOMAINS)

    def test_doc_table_matches_config(self):
        """docs/딴짓_사이트_목록.md 표와 코드 목록이 같아야 한다 (같은 PR에서 함께 고친다)."""
        documented = doc_domains()
        self.assertEqual(sorted(documented - DISTRACTION_DOMAINS), [], "문서에만 있음")
        self.assertEqual(sorted(DISTRACTION_DOMAINS - documented), [], "코드에만 있음")


class VideoPolicyTest(unittest.TestCase):
    """유튜브 영상 분류표 (WIN-05)."""

    def test_every_youtube_category_has_a_row(self):
        """카테고리를 새로 넣으면 판정도 정해야 한다 (빠지면 기본값 미분류로 조용히 흘러감)."""
        self.assertEqual(sorted(set(CATEGORY_NAMES.values()) - set(VIDEO_POLICIES)), [])

    def test_keys_are_youtube_names_or_special_classes(self):
        self.assertEqual(sorted(set(VIDEO_POLICIES) - set(CATEGORY_NAMES.values()) - {SHORTS, BGM}), [])

    def test_values_are_valid(self):
        for name, policy in [*VIDEO_POLICIES.items(), ("default", DEFAULT_VIDEO_POLICY)]:
            with self.subTest(name=name):
                self.assertIn(policy.foreground, WINDOW_LABELS)
                self.assertGreaterEqual(policy.grace_sec, 0)
                if policy.foreground != "distraction":
                    self.assertEqual(policy.grace_sec, 0, "유예는 딴짓 분류에만 의미가 있다")
                    self.assertFalse(policy.background_distraction, "작업·미분류 영상을 틀어 둔 것은 감점하지 않는다")

    def test_grace_principle(self):
        """틀어 두기를 허용하는 딴짓 분류는 고르는 시간(유예)이 있고, 틀어 두기도 딴짓이면 유예 없이 바로."""
        for name, policy in VIDEO_POLICIES.items():
            if policy.foreground != "distraction":
                continue
            with self.subTest(name=name):
                if policy.background_distraction:
                    self.assertEqual(policy.grace_sec, 0)
                else:
                    self.assertGreater(policy.grace_sec, 0)

    def test_study_videos_and_music(self):
        self.assertEqual(VIDEO_POLICIES["Education"].foreground, "work")
        self.assertEqual(VIDEO_POLICIES["Science & Technology"].foreground, "work")
        music = VIDEO_POLICIES["Music"]
        self.assertEqual((music.foreground, music.background_distraction), ("distraction", False))
        self.assertGreater(music.grace_sec, 0)
        self.assertEqual(VIDEO_POLICIES[BGM], music)

    def test_clear_distractions(self):
        """2026-11-09 결정한 딴짓 카테고리와 쇼츠는 보든 틀어 두든 딴짓."""
        for name in ("Gaming", "Entertainment", "Comedy", "Film & Animation", "Sports", "People & Blogs", SHORTS):
            with self.subTest(name=name):
                self.assertEqual(VIDEO_POLICIES[name], VideoPolicy("distraction", 0, True))

    def test_bgm_patterns_compile(self):
        for pattern in BGM_TITLE_PATTERNS:
            re.compile(pattern)

    def test_doc_table_matches_config(self):
        """docs/유튜브_영상_분류표.md 표와 코드 표가 같아야 한다 (같은 PR에서 함께 고친다)."""
        self.assertEqual(doc_video_policies(), VIDEO_POLICIES)


if __name__ == "__main__":
    unittest.main()
