"""현장설명회 일시 — 나라장터 공고 칸에서 읽어 카드 맨 위·일정 팝업에(2026-11-24 사용자 요청).
계기: 노원 하나금융공동 직장어린이집(R26BK01836585) 11/19 게시 → 11/20 14:00 설명회, 참석 업체만 투찰. 공고 칸은 형식만 따른 가짜."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import fields as F  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.screen import build_schedule  # noqa: E402
from tests import fixtures  # noqa: E402
from tests.test_team_ui import HELD, RUN, card  # noqa: E402
from webapp import mview  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_named_field_and_schedule():
    raw = fixtures.notice("R26BK01836585", dcmtgOprtnDt="2026-11-20 14:00:00", dcmtgOprtnPlce="서울 노원구 현장")
    n = notice_from_raw(raw, "공사")
    assert n.site_briefing_at == "2026-11-20 14:00:00" and n.site_briefing_place == "서울 노원구 현장"
    deadlines = dict(build_schedule(n).all_deadlines)
    assert deadlines["현장설명회"] == datetime(2026, 11, 20, 14, 0)
    assert build_schedule(n).earliest[0] != "현장설명회", "설명회는 카드 마감 계산에 안 씀"


def test_other_dcmtg_field_names_and_none():
    at, place = F.pick_briefing({"dcmtgOprtnDtTm": "202611201400", "dcmtgPlceNm": "본사 회의실", "bidNtceNo": "X"})
    assert at == "202611201400" and place == "본사 회의실"
    assert F.pick_briefing({"bidNtceNo": "X", "dcmtgOprtnDt": ""}) == (None, None)


def test_view_carries_briefing():
    run = {**RUN, "candidates": [card("R26BK00000009", site_briefing_at="2099-01-01T14:00", site_briefing_place="현장")]}
    item = next(i for i in mview.notices(run, {}, HELD)["items"] if i["noticeNo"] == "R26BK00000009")
    assert item["siteBriefingAt"] == "2099-01-01T14:00" and item["siteBriefingPlace"] == "현장"


def test_screen_puts_upcoming_briefing_first():
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    assert "briefingBadge(n)" in html and re.search(r"\[\.\.\.ahead, \.\.\.sorted\.filter", html)
