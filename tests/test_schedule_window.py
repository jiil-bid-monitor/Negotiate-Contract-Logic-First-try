"""매일/주간 실행용 조회 기간(`complete_days_window`)과 Supabase 저장 규칙(`rejected_row`) 테스트.

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates, complete_days_window  # noqa: E402
from nego.supabase_repo import rejected_row  # noqa: E402
from tests import fixtures  # noqa: E402

NOW = datetime(2026, 9, 1, 8, 5)


def _candidates(n: int, award: str = fixtures.QUALIFY):
    config = load_config()
    config.screen.keywords = ["전시관"]
    raws = [fixtures.notice(f"R26TG{i:04d}", title=f"○○전시관 전시물 제작설치 {i}", award=award) for i in range(n)]
    stats = RunStats(period_begin=datetime(2026, 8, 31), period_end=datetime(2026, 8, 31, 23, 59))
    candidates = build_candidates([notice_from_raw(r, "용역") for r in raws], config, {}, {}, NOW, stats, {"입찰"})
    return candidates, stats


class TestCompleteDaysWindow(unittest.TestCase):
    def test_daily_window_is_yesterday_whole_day(self):
        begin, end = complete_days_window(datetime(2026, 9, 29, 8, 0), 1)
        self.assertEqual(begin, datetime(2026, 9, 28, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 28, 23, 59))

    def test_consecutive_runs_do_not_overlap_or_gap(self):
        """스케줄이 늦게 돌아도(08:47) 구간은 같다 — 같은 공고를 두 번 보내지 않는다."""
        _, end_mon = complete_days_window(datetime(2026, 9, 28, 8, 0), 1)
        begin_tue, _ = complete_days_window(datetime(2026, 9, 29, 8, 47), 1)
        self.assertEqual((begin_tue - end_mon).total_seconds(), 60)

    def test_weekly_window(self):
        begin, end = complete_days_window(datetime(2026, 9, 28, 8, 10), 7)
        self.assertEqual(begin, datetime(2026, 9, 21, 0, 0))
        self.assertEqual(end, datetime(2026, 9, 27, 23, 59))


class TestRejectedRow(unittest.TestCase):
    def test_non_negotiated_rejected_rows_drop_raw(self):
        candidates, _ = _candidates(1)
        row = rejected_row(candidates[0])
        self.assertIsNone(row["raw"])
        self.assertEqual(row["title"], candidates[0].notice.title)

    def test_negotiated_rejected_rows_keep_raw(self):
        candidates, _ = _candidates(1)
        candidates[0].category = "협상"
        self.assertIsNotNone(rejected_row(candidates[0])["raw"])


if __name__ == "__main__":
    unittest.main()


def test_registration_deadline_before_submission():
    """거제 지심도 산마루문화놀이터 제안공모 — 공고문엔 응모신청서 등록(10/13 14:00), 지침서엔 제안서 제출(11/17)만 있어
    D-46으로 보였음(2026-10-02). 등록 마감도 찾고, 화면 마감은 가장 이른 것."""
    from datetime import datetime
    from nego.schedule_text import extract_deadlines

    notice = ("응모신청서\n등록\n ◦ 응모신청서 등록 일시 : 2026. 10. 13.(화) 13:00 ~ 14:00\n"
              " ◦ 응모신청서 등록 장소 : 거제시 일운면 옥림리 산1 지심도 휴게소\n")
    guide = "제안서 제출기한 : 2026. 11. 17.(화) 09:00~18:00\n"
    assert extract_deadlines(notice) == [("첨부 참가등록 마감", datetime(2026, 10, 13, 14, 0))]
    assert extract_deadlines(guide) == [("서류 제출기한", datetime(2026, 11, 17, 18, 0))]


def test_all_deadlines_sorted_for_popup():
    """검단 박물관·도서관 복합문화시설 — 공동수급협정 마감(10/19)이 가장 일러 카드 마감이 되는데, 팝업엔 자격등록(11/09)도 보이게."""
    from datetime import datetime
    from nego.screen import Schedule

    s = Schedule(qualification_deadline=datetime(2026, 11, 9, 18), joint_agreement_deadline=datetime(2026, 10, 19, 18),
                 bid_deadline=datetime(2026, 11, 10, 10))
    assert s.earliest == ("자격등록 마감", datetime(2026, 11, 9, 18))  # 카드는 자격등록 기준(2026-10-02 사용자 결정)
    assert [label for label, _ in s.all_deadlines] == ["공동수급협정 마감", "자격등록 마감", "입찰 마감"]


def test_joint_only_api_uses_attachment_deadline_and_spaced_table_header():
    """고흥분청문화박물관 — 나라장터엔 공동수급협정서 제출(10/13 17:00)만 있고, 공고문 표 "제 출 일 시 … 2026. 10. 13.(화)
    13:00 ~ 17:00"(글자 사이를 띄운 머리글)를 못 읽어 카드가 '공동수급협정 마감'으로만 보였음(2026-10-02)."""
    from datetime import datetime
    from nego.schedule_text import extract_deadlines
    from nego.screen import Schedule

    table = ("4. 입찰참가등록 및 제안서 제출\n제 출 일 시 제출장소 제출방법 제 출 자 제출서류\n"
             "2026. 10. 13.(화)\n13:00 ~ 17:00\n(평가위원추첨 17:00~17:30)\n")
    found = extract_deadlines(table)
    assert found == [("서류 제출기한", datetime(2026, 10, 13, 17, 0))]
    # 흔한 낱말 "마감" 뒤 줄바꿈 너머의 날짜는 잡지 않는다
    assert extract_deadlines("도장 마감\n친환경 흡음텍스\n(제공: 공공시설건축과, 2022.6.30.)") == []

    s = Schedule(qualification_deadline=None, joint_agreement_deadline=datetime(2026, 10, 13, 17), bid_deadline=None)
    s.attachment_deadline_kind, s.attachment_deadline = found[0]
    assert s.joint_only
    assert s.earliest == ("서류 제출기한", datetime(2026, 10, 13, 17, 0))
    assert [label for label, _ in s.all_deadlines] == ["공동수급협정 마감", "서류 제출기한"]


def test_bid_registration_table_label():
    """2026-10-20 제보(UAE 두바이 의료기기전시회 한국관) — 나라장터 일정 칸이 비고 공고문 표에만 "입찰 등록 및 서류접수"."""
    from nego.schedule_text import extract_deadlines

    text = "공 통 사 항\n입찰 등록 및 서류접수\n 2026년 10월 1일(목) 17:00 까지 제출\n※ 메일 송부 및 최종확인 후 원본 접수 요망"
    assert extract_deadlines(text) == [("첨부 참가등록 마감", datetime(2026, 10, 1, 17, 0))]
    # 참가자격 문장의 "입찰등록 마감일시까지"는 날짜가 없어 잡지 않는다
    assert extract_deadlines("입찰등록에 필요한 소정의 서류를 구비하여 입찰등록 마감일시까지 등록을 필한 자") == []
