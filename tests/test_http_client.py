"""장기간 조회 청크 분할 단위 테스트. 표준 라이브러리 unittest만 사용한다.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from nego.http_client import ApiConfig, ApiError, DataGoKrClient, chunk_date_range  # noqa: E402


class TestServiceKeyMasked(unittest.TestCase):
    def test_connection_error_message_hides_service_key(self):
        """연결 오류 문구엔 요청 주소가 통째로 들어간다 — 로그·개발자 알림에 키가 새면 안 된다."""

        class Down:
            def get(self, url, timeout=None):
                raise requests.ConnectionError(f"Max retries exceeded with url: {url} (Caused by timeout)")

        client = DataGoKrClient(ApiConfig(service_key="MY+SECRET/KEY=", max_retries=0, retry_delay_sec=0), Down())
        with self.assertRaises(ApiError) as ctx:
            client.fetch_all_pages("https://apis.data.go.kr/x", "op", {}, "본공고/용역")
        msg = str(ctx.exception)
        self.assertIn("serviceKey=***", msg)
        self.assertNotIn("SECRET", msg)


class TestRetryDelays(unittest.TestCase):
    def test_explicit_delays_used_in_order(self):
        """API_RETRY_DELAYS처럼 간격을 직접 주면 그 순서대로 쉰다 (2배씩 아님)."""
        from unittest import mock

        class Down:
            def get(self, url, timeout=None):
                raise requests.ConnectionError("timeout")

        client = DataGoKrClient(ApiConfig(service_key="k", max_retries=4, retry_delays=(10, 10, 30, 70)), Down())
        with mock.patch("nego.http_client.time.sleep") as sleep, self.assertRaises(ApiError):
            client.fetch_all_pages("https://apis.data.go.kr/x", "op", {}, "본공고/용역")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [10, 10, 30, 70])

    def test_default_doubles(self):
        from unittest import mock

        class Down:
            def get(self, url, timeout=None):
                raise requests.ConnectionError("timeout")

        client = DataGoKrClient(ApiConfig(service_key="k", max_retries=3, retry_delay_sec=1), Down())
        with mock.patch("nego.http_client.time.sleep") as sleep, self.assertRaises(ApiError):
            client.fetch_all_pages("https://apis.data.go.kr/x", "op", {}, "본공고/용역")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4])


class TestChunkDateRange(unittest.TestCase):
    def test_short_range_is_a_single_chunk(self):
        chunks = list(chunk_date_range("202609010000", "202609150000", days=30))
        self.assertEqual(chunks, [("202609010000", "202609150000")])

    def test_long_range_is_split_into_non_overlapping_chunks(self):
        chunks = list(chunk_date_range("202601010000", "202603020000", days=30))
        self.assertGreater(len(chunks), 1)
        fmt = "%Y%m%d%H%M"
        for (_, prev_end), (next_begin, _) in zip(chunks, chunks[1:]):
            gap = datetime.strptime(next_begin, fmt) - datetime.strptime(prev_end, fmt)
            self.assertEqual(gap, timedelta(minutes=1), "청크 사이에 빈틈/겹침이 없어야 한다")

    def test_chunks_cover_the_whole_range(self):
        chunks = list(chunk_date_range("202601010000", "202603020000", days=30))
        self.assertEqual(chunks[0][0], "202601010000")
        self.assertEqual(chunks[-1][1], "202603020000")


class _RecordingSession:
    """URL의 조회기간 파라미터를 기록하고, 청크마다 서로 다른 건을 돌려준다."""

    def __init__(self):
        self.requested_ranges: list[tuple[str, str]] = []

    def get(self, url, timeout=None):
        query = parse_qs(urlparse(url).query)
        begin = query["inqryBgnDt"][0]
        end = query["inqryEndDt"][0]
        self.requested_ranges.append((begin, end))

        body = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "OK"},
                "body": {"items": [{"bidNtceNo": f"{begin}-{end}"}], "totalCount": 1},
            }
        }
        return _FakeResponse(json.dumps(body))


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class TestFetchAllPagesChunked(unittest.TestCase):
    def test_splits_long_range_into_multiple_requests_and_merges_results(self):
        session = _RecordingSession()
        client = DataGoKrClient(
            ApiConfig(service_key="key", max_retries=0, num_of_rows=999, max_pages=10), session=session
        )

        items = client.fetch_all_pages_chunked(
            "https://example.com", "op", {"inqryDiv": "1"}, "202601010000", "202603020000", "테스트", max_days=30
        )

        self.assertGreater(len(session.requested_ranges), 1, "30일을 넘는 기간은 여러 번 나눠 호출해야 한다")
        self.assertEqual(len(items), len(session.requested_ranges), "청크마다 받은 결과가 모두 합쳐져야 한다")

    def test_short_range_makes_a_single_request(self):
        session = _RecordingSession()
        client = DataGoKrClient(ApiConfig(service_key="key", max_retries=0), session=session)

        client.fetch_all_pages_chunked(
            "https://example.com", "op", {"inqryDiv": "1"}, "202609010000", "202609150000", "테스트"
        )

        self.assertEqual(len(session.requested_ranges), 1)


if __name__ == "__main__":
    unittest.main()
