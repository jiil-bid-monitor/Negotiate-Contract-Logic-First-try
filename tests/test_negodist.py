"""협상 공고 '다른 업체 최저가' 분포(nego.negodist) — 가짜 개찰 자료로."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from nego import negodist  # noqa: E402


def rec(no, amounts, award="협상에의한계약", basis=100_000_000, work="용역", title="○○ 전시관 전시물 제작 설치", opened="2026-05-01 10:00",
        **extra):
    info = {"title": title, "award_method": award, "opened_at": opened, "basis": basis, **extra}
    return {"no": no, "work": work, "notice": info,
            "bidders": [{"bidprcAmt": str(a), "prcbdrBizno": f"{i:010d}"} for i, a in enumerate(amounts)]}


def test_from_record_rates_and_base_fallback():
    bid, why = negodist.from_record(rec("A", [92_000_000, 88_000_000, 95_000_000]))
    assert why == "" and bid.lowest_rate == 88.0 and bid.second_rate == 92.0 and bid.base_source == "기초금액"
    bid, _ = negodist.from_record(rec("B", [90_000_000, 99_000_000], basis=None, est_price=100_000_000))
    assert bid.base == 110_000_000 and bid.base_source == "추정가격×1.1" and round(bid.lowest_rate, 2) == 81.82


def test_from_record_skips():
    assert negodist.from_record(rec("A", [1], award="적격심사"))[0] is None
    assert negodist.from_record(rec("A", [1], award="")) == (None, "낙찰방법 모름(--fill-award 필요)")
    assert negodist.from_record(rec("A", [90_000_000], contract_method="제한경쟁(지명)"))[1] == "지명경쟁"
    assert negodist.from_record(rec("A", [90_000_000], basis=None))[1] == "기준 금액 없음"
    assert negodist.from_record(rec("A", [9_000_000]))[1] == "최저가 비율 이상값"


def test_own_bidder_excluded():
    r = rec("A", [80_000_000, 90_000_000])
    r["bidders"][0]["prcbdrBizno"] = "206-86-36338"
    bid, _ = negodist.from_record(r, own_bizno="2068636338")
    assert bid.lowest_rate == 90.0 and bid.bidders == 1


def test_load_summarize_and_columns(tmp_path):
    lines = [rec(f"N{i}", [100_000_000 * (0.80 + i * 0.01), 99_000_000]) for i in range(20)]
    lines.append(rec("N0", [70_000_000]))  # 같은 공고 업체 적은 줄 — 많은 쪽을 씀
    lines.append(rec("X", [1], award="적격심사"))
    lines.append({"no": "Y", "error": "못 찾음"})
    (tmp_path / "bid_results_others.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    (tmp_path / "bid_results_채우기전.jsonl").write_text(json.dumps(rec("Z", [50_000_000, 60_000_000])), encoding="utf-8")
    bids, skipped = negodist.load(negodist.history_paths(tmp_path))
    assert len(bids) == 20 and skipped == {"조회 오류": 1} and not any(b.no == "Z" for b in bids)
    s = negodist.summarize(bids, work="용역", title="△△ 체험관 전시 콘텐츠 제작")
    assert s["n"] == 20 and s["quantiles"][50] == 89.5 and s["below80"] == 0
    cols = s["columns"]
    assert [c["pct"] for c in cols] == [90, 75, 50, 25, 10] and cols[2]["median"] and cols[0]["rate"] >= cols[-1]["rate"]
    assert negodist.summarize(bids, work="공사")["scope"] == "협상 공고 전체"  # 공사 표본이 없으면 넓힘
    text = negodist.report(bids, skipped, 0)
    assert "협상 공고 20건" in text or "쓸 수 있는 협상 공고 20건" in text


def test_find_targets_pick_nego():
    import find_bid_targets as ft

    rows = [
        {"bidNtceNo": "R1", "bidNtceOrd": "000", "bidNtceNm": "전시관 전시물 제작 설치", "sucsfbidMthdNm": "협상에 의한 계약",
         "cntrctCnclsMthdNm": "제한경쟁", "opengDt": "2026-05-01 10:00"},
        {"bidNtceNo": "R1", "bidNtceOrd": "001", "bidNtceNm": "전시관 전시물 제작 설치", "sucsfbidMthdNm": "협상에의한계약",
         "cntrctCnclsMthdNm": "제한경쟁", "opengDt": "2026-05-03 10:00"},
        {"bidNtceNo": "R2", "bidNtceNm": "전시관 전시물 제작 설치", "sucsfbidMthdNm": "적격심사", "opengDt": "2026-05-01 10:00"},
        {"bidNtceNo": "R3", "bidNtceNm": "전시관 전시물 제작 설치", "sucsfbidMthdNm": "협상에의한계약", "opengDt": "2026-12-01 10:00"},
        {"bidNtceNo": "R4", "bidNtceNm": "전시관 전시물 제작 설치", "sucsfbidMthdNm": "협상에의한계약", "cntrctCnclsMthdNm": "수의계약",
         "opengDt": "2026-05-01 10:00"},
    ]
    got = ft.pick_nego(rows, "용역", None, "2026-11-12 09:00")
    assert list(got) == ["R1"] and got["R1"]["공고번호"] == "R1-001"


def test_negodist_not_read_as_bid_history(tmp_path):
    from nego import bidrecommend

    (tmp_path / "nego_results.jsonl").write_text("{}", encoding="utf-8")
    assert bidrecommend.history_paths(tmp_path) == []
    assert negodist.history_paths(tmp_path) == [tmp_path / "nego_results.jsonl"]
