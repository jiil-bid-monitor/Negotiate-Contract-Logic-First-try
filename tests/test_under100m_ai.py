import json
import unittest
from types import SimpleNamespace

from under100m import ai_summary


class FakeClient:
    def __init__(self, data):
        block = SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(stop_reason="end_turn", content=[block])))


class AiSummaryTest(unittest.TestCase):
    def test_drops_items_without_real_quote(self):
        doc = "■ 제출 서류\n1. 사업자등록증 사본 1부\n6. 영인 및 족자 장황 제작사양서 1부"
        data = {"overview": "영인본 제작", "notes": [], "conditions": [],
                "documents": [{"text": "제작사양서", "quote": "영인 및 족자 장황  제작사양서 1부"},
                              {"text": "법인인감증명서", "quote": "법인인감증명서 1부"}]}
        ai = ai_summary.summarize([("업체 조건 및 제출 서류.hwp", doc)], client=FakeClient(data))
        self.assertEqual([i["text"] for i in ai["documents"]], ["제작사양서"])
        self.assertIn("- 제작사양서", ai_summary.lines(ai))

    def test_no_key_returns_none(self):
        import os
        old = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        try:
            self.assertIsNone(ai_summary.summarize([("a", "글")]))
        finally:
            os.environ.update({k: v for k, v in old.items() if v})


if __name__ == "__main__":
    unittest.main()
