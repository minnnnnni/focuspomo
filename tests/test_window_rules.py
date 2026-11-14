"""config/window_rules.py 딴짓 규칙 목록 검사 (WIN-04, 점수 명세 2.1)."""
import re
import unittest
from pathlib import Path

from config.window_rules import (
    DISTRACTION_DOMAIN_GROUP_KO,
    DISTRACTION_DOMAIN_GROUPS,
    DISTRACTION_DOMAINS,
    DISTRACTION_VIDEO_CATEGORIES,
)
from window_tracker.domain_probe import url_to_domain
from window_tracker.youtube_probe import CATEGORY_NAMES

DOC = Path(__file__).resolve().parents[1] / "docs" / "딴짓_사이트_목록.md"


def doc_domains() -> set[str]:
    """문서의 '## 딴짓 사이트' 표에서 도메인 열(백틱)만 읽는다."""
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## 딴짓 사이트", 1)[1].split("\n## ", 1)[0]
    return {m for line in section.splitlines() if line.startswith("|")
            for m in re.findall(r"`([^`]+)`", line)}


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


class VideoCategoryTest(unittest.TestCase):
    def test_categories_are_youtube_names(self):
        self.assertLessEqual(DISTRACTION_VIDEO_CATEGORIES, set(CATEGORY_NAMES.values()))

    def test_study_categories_are_not_listed(self):
        for category in ("Music", "Education", "Science & Technology"):
            self.assertNotIn(category, DISTRACTION_VIDEO_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
