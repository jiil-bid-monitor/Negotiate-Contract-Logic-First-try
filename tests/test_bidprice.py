"""투찰가격 분석용 개찰 결과 수집(`nego/bidprice.py`) 테스트. 실제 API는 부르지 않는다."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import bidprice  # noqa: E402
from nego.models import Notice  # noqa: E402


def _notice(**kw):
    base = dict(work_type="공사", notice_no="R26BK00000001", notice_ord="000", title="○○ 인테리어 공사",
                demand_institution="○○시", opening_at="2026-09-30 11:00:00", raw={"sucsfbidLwltRate": "87.745"})
    base.update(kw)
    return Notice(**base)


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def fetch_all_pages(self, base_url, operation, params, label):
        self.calls.append((operation, params))
        return self.responses.get(operation, [])


class BidPriceTest(unittest.TestCase):
    def test_prepare_prices_and_bidders(self):
        prices = [
            {"bidNtceOrd": "000", "compnoRsrvtnPrceSno": str(i), "bsisPlnprc": str(100_000_000 + i * 100_000),
             "drwtYn": "Y" if i in (2, 5, 9, 14) else "N", "plnprc": "100625000", "bssamt": "100000000"}
            for i in range(1, 16)
        ]
        bidders = [
            {"bidNtceOrd": "000", "opengRank": "2", "prcbdrNm": "B사", "prcbdrBizno": "222", "bidprcAmt": "88500000", "bidprcrt": "87.950"},
            {"bidNtceOrd": "000", "opengRank": "1", "prcbdrNm": "지일", "prcbdrBizno": "2068636338", "bidprcAmt": "88400000", "bidprcrt": "87.850"},
        ]
        client = FakeClient({
            bidprice.PREPARE_PRICE_OPERATIONS["공사"]: prices,
            bidprice.F.OPENING_BIDDERS_OPERATION: bidders,
        })
        result = bidprice.fetch_result(client, "http://x", _notice())
        assert result.assessment_rate == 100.625
        assert len(result.drawn_prices) == 4
        assert result.winner.name == "지일" and result.winner.rate == 87.85
        assert all(params["inqryDiv"] == "2" and params["bidNtceNo"] == "R26BK00000001" for _, params in client.calls)
        text = bidprice.summarize([result], own_bizno="2068636338")
        assert "사정률" in text and "+0.105%p" in text and "지일 참가 1건" in text and "1위" in text


    def test_basis_info_range_and_a_value(self):
        rows = [{"bidNtceNo": "R26BK00000001", "bidNtceOrd": "000", "rsrvtnPrceRngBgnRate": "-3", "rsrvtnPrceRngEndRate": "3",
                 "npnInsrprm": "2000000", "sftyMngcst": "1000000", "bidPrceCalclAYn": "N"}]
        self.assertEqual(bidprice.basis_info(rows, "R26BK00000001"), (-3.0, 3.0, 0.0))  # A값 미적용 공고면 0
        rows[0]["bidPrceCalclAYn"] = "Y"
        self.assertEqual(bidprice.basis_info(rows, "R26BK00000001"), (-3.0, 3.0, 3_000_000.0))
        self.assertEqual(bidprice.basis_info([], "R26BK00000001"), (None, None, None))

    def test_opened_only_past(self):
        now = datetime(2026, 10, 12, 9, 0)
        assert bidprice._opened(_notice(opening_at="2026-10-01 10:00:00"), now)
        assert not bidprice._opened(_notice(opening_at="2026-10-20 10:00:00"), now)
        assert not bidprice._opened(_notice(opening_at=None), now)


    def test_api_error_kept_as_error(self):
        class Broken(FakeClient):
            def fetch_all_pages(self, *a):
                raise bidprice.ApiError("예비가격", "끊김")

        result = bidprice.fetch_result(Broken({}), "http://x", _notice())
        assert result.error and result.assessment_rate is None
        assert "조회 실패 1건" in bidprice.summarize([result])



if __name__ == "__main__":
    unittest.main()
