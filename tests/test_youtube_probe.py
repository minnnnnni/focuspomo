"""window_tracker/youtube_probe.py 테스트 — 실제 API 호출 없이 가짜 응답으로 확인."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from window_tracker.youtube_probe import YouTubeCategoryLookup, extract_video, load_api_key

VID = "dQw4w9WgXcQ"


def fake_response(category_id="20", title="롤 하이라이트", channel="채널"):
    return {"items": [{"snippet": {"categoryId": category_id, "title": title, "channelTitle": channel}}]}


class ExtractVideoTest(unittest.TestCase):
    def test_address_bar_formats(self):
        self.assertEqual(extract_video(f"youtube.com/watch?v={VID}"), ("watch", VID))  # Chrome 기본 표시
        self.assertEqual(extract_video(f"https://www.youtube.com/watch?v={VID}&t=42s"), ("watch", VID))
        self.assertEqual(extract_video(f"m.youtube.com/watch?list=PL1&v={VID}"), ("watch", VID))
        self.assertEqual(extract_video(f"youtu.be/{VID}"), ("watch", VID))
        self.assertEqual(extract_video(f"youtube.com/shorts/{VID}"), ("shorts", VID))
        self.assertEqual(extract_video(f"youtube.com/live/{VID}"), ("live", VID))
        self.assertEqual(extract_video(f"music.youtube.com/watch?v={VID}"), ("watch", VID))

    def test_not_a_video_page(self):
        for text in ["youtube.com", "youtube.com/results?search_query=os", "youtube.com/@channel",
                     "naver.com", "youtube.com/watch?v=short", "롤 하이라이트", None]:
            self.assertIsNone(extract_video(text), text)


class LookupTest(unittest.TestCase):
    def test_lookup_and_cache(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return fake_response("20")

        lookup = YouTubeCategoryLookup("test-key", fetch_json=fetch)
        self.assertEqual(lookup.lookup(VID).category, "Gaming")
        self.assertEqual(lookup.lookup(VID).category, "Gaming")
        self.assertEqual(len(calls), 1)  # 같은 영상은 한 번만 요청
        self.assertIn("part=snippet", calls[0])

    def test_unknown_category_id(self):
        lookup = YouTubeCategoryLookup("k", fetch_json=lambda u, t: fake_response("44"))
        self.assertEqual(lookup.lookup(VID).category, "Unknown(44)")

    def test_deleted_video_is_cached_none(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            return {"items": []}

        lookup = YouTubeCategoryLookup("k", fetch_json=fetch)
        self.assertIsNone(lookup.lookup(VID))
        self.assertIsNone(lookup.lookup(VID))
        self.assertEqual(len(calls), 1)

    def test_network_error_is_retried_later(self):
        calls = []

        def fetch(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("HTTP 403: quotaExceeded")
            return fake_response("27")

        lookup = YouTubeCategoryLookup("k", fetch_json=fetch)
        with self.assertLogs("window_tracker.youtube_probe", level="WARNING"):
            self.assertIsNone(lookup.lookup(VID))
        self.assertEqual(lookup.lookup(VID).category, "Education")

    def test_no_api_key_makes_no_request(self):
        def fetch(url, timeout):
            raise AssertionError("호출되면 안 됨")

        self.assertIsNone(YouTubeCategoryLookup("", fetch_json=fetch).lookup(VID))

    def test_category_by_title(self):
        lookup = YouTubeCategoryLookup("k", fetch_json=lambda u, t: fake_response("20", title="롤  하이라이트 모음"))
        self.assertIsNone(lookup.category_for_title("롤 하이라이트 모음"))  # 아직 조회 전
        lookup.lookup(VID)
        self.assertEqual(lookup.category_for_title("롤 하이라이트 모음"), "Gaming")  # 공백 정규화
        self.assertIsNone(lookup.category_for_title("다른 영상"))
        self.assertIsNone(lookup.category_for_title(None))


class ApiKeyTest(unittest.TestCase):
    def test_reads_env_file_with_bom(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("YOUTUBE_API_KEY", None)
            env = Path(d) / ".env"
            env.write_text('\ufeffOTHER=1\nYOUTUBE_API_KEY="abc123"\n', encoding="utf-8")
            self.assertEqual(load_api_key(env), "abc123")
            self.assertIsNone(load_api_key(Path(d) / "없는파일"))
            os.environ["YOUTUBE_API_KEY"] = "from-env"
            self.assertEqual(load_api_key(env), "from-env")


if __name__ == "__main__":
    unittest.main()
