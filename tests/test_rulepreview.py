"""수집 조건 영향 미리 보기 (2026-11-16 사용자 요청 — 판단 기록으로 조건을 조금씩 보정하기 전에 확인)."""
import json
import tempfile
import unittest
from pathlib import Path

from nego.screen import ScreenConfig
from webapp import rulepreview as R
from webapp.store import Store


def _row(no, title, cand, reason=None, **kw):
    return {"notice_no": no, "notice_ord": "000", "title": title, "kind": "본공고", "work_type": "용역",
            "is_candidate": cand, "excluded_reason": reason, "budget": 100000000, **kw}


class RulePreviewTest(unittest.TestCase):
    cfg = ScreenConfig(keywords=["박물관", "통신"], exclude_keywords=["운영"], hard_exclude_keywords=["특별전"], review_conflicts=True)

    def payload(self):
        return {"candidates": [
            _row("R1", "박물관 희귀자료 영인 해제", True),
            _row("R2", "박물관 전시물 제작", True),
            _row("R3", "선거 통신장비 배부 및 회수", True),
            _row("R4", "금속창호 낙석방지책 정비공사", True, license_match=["금속창호·지붕건축물조립공사업(4991)"]),
        ], "rejected": [
            _row("R5", "체험관 운영 용역", False, "제외키워드 (운영)"),
            _row("R6", "도로 포장 공사", False, "보유면허없음 (요구 면허 토목공사업 — 지일 보유 없음, 공동수급 불가)"),
            _row("R7", "체험관 조성 사업", False, "미매칭 (키워드·품명코드 모두 불일치)", only_keyword_miss=True),
        ]}

    def test_exclude_keyword_shows_dropped_notices_with_judgment(self):
        change = R.Change(add_exclude=["영인", "배부"])
        rows = R.report(R.compare(self.payload(), self.cfg, R.apply_change(self.cfg, change)),
                        {"R3": {"status": "불참", "tags": ["분야 다름"]}})
        got = {r[1]: (r[0], r[8], r[9]) for r in rows}
        # '영인' 공고는 관심 키워드(박물관)도 있어 웹앱에선 '검토 필요'로, '배부' 공고도 관심 키워드(통신)가 있어 검토 필요
        self.assertEqual(got["R1"][0], "구역 바뀜")
        self.assertEqual(got["R3"], ("구역 바뀜", "검토 필요", "불참 (분야 다름)"))
        self.assertNotIn("R2", got)
        self.assertNotIn("R4", got)

    def test_hard_exclude_drops_even_license_route(self):
        rows = R.report(R.compare(self.payload(), self.cfg, R.apply_change(self.cfg, R.Change(add_hard=["정비", "영인"]))), {})
        got = {r[1]: r for r in rows}
        self.assertEqual(got["R4"][0], "새로 빠짐")
        self.assertEqual(got["R1"][0], "새로 빠짐")

    def test_new_keyword_brings_in_only_screen_stage_rejects(self):
        rows = R.report(R.compare(self.payload(), self.cfg, R.apply_change(self.cfg, R.Change(add_keyword=["체험관", "포장"], remove_exclude=["운영"]))), {})
        got = {r[1]: r for r in rows}
        self.assertEqual((got["R7"][0], got["R7"][10]), ("새로 들어옴", ""))  # 면허·지역·마감도 통과했던 공고
        self.assertEqual(got["R5"][0], "새로 들어옴")
        self.assertIn("다시 확인", got["R5"][10])
        self.assertNotIn("R6", got)  # 면허 단계로 빠진 공고는 키워드를 바꿔도 그대로

    def test_cli_writes_csv(self):
        d = Path(tempfile.mkdtemp())
        store = Store(d / "web.sqlite3")
        store.save_run("2026-11-16T09:00:00", {"days": 7}, self.payload())
        (d / "participation.json").write_text(json.dumps({"R1": {"status": "불참", "tags": []}}), encoding="utf-8")
        code = R.main(["--add-hard", "영인", "--db", str(d / "web.sqlite3"), "--out", str(d / "out")])
        self.assertEqual(code, 0)
        csv_file = next((d / "out").glob("규칙미리보기_*.csv"))
        text = csv_file.read_text(encoding="utf-8-sig")
        self.assertIn("무조건 제외 추가 영인", text)
        self.assertIn("R1,박물관 희귀자료 영인 해제", text)
        self.assertIn("불참", text)


if __name__ == "__main__":
    unittest.main()
