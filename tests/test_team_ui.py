"""팀원 화면 이식(2026-10-19 B안) — 화면이 부르는 API 모양(webapp/mview.py), 참가여부·올리기 저장(webapp/mstore.py),
서버 주소·쿠키 확인."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_webapp import FakePast, config_with_key, fixture_run  # noqa: E402
from webapp import mview  # noqa: E402
from webapp.collect import Collector  # noqa: E402
from webapp.mstore import TeamStore  # noqa: E402
from webapp.server import App, make_handler  # noqa: E402
from webapp.store import Store  # noqa: E402

HELD = {"heldProducts": [{"code": "6010989901", "name": "실물모형및전시물"}], "companyRegion": "강원특별자치도 정선군"}


def card(no, **kw):
    base = {
        "notice_no": no, "notice_ord": "000", "title": f"{no} 과학관 전시물", "kind": "본공고", "work_type": "용역",
        "category": "협상", "is_candidate": True, "excluded_reason": None, "confidence": "참고용", "section": "관심",
        "matched_keywords": ["과학관"], "matched_product_codes": [], "product_codes": [], "deadline": "2099-01-02T18:00",
        "deadline_label": "자격등록 마감", "deadlines": [["자격등록 마감", "2099-01-02T18:00"], ["입찰 마감", "2099-01-03T10:00"]],
        "budget": 300000000, "budget_kind": "추정가격", "qualification": {"checked": True, "passes": True, "summary": "", "missing": [], "parts": []},
        "region_check": {"status": "미확인", "required": []}, "joint": {"label": "공동이행", "allowed": True},
        "sync": {"score": 0.7, "level": "높음", "basis": "과업 원문", "tags": {}, "top": []},
    }
    base.update(kw)
    return base


RUN = {
    "id": 1, "started_at": "2026-10-19T09:00:00", "finished_at": "2026-10-19T09:05:00", "params": {"days": 7},
    "format": 40, "keywords": ["과학관"],
    "candidates": [
        card("R26BK00000001"),
        card("R26BK00000002", confidence="검토필요", review_exclude="운영 용역", product_codes=["6010989901"]),
        card("R26BK00000003", section="세부품명", matched_keywords=[], matched_product_codes=["6010989901"], product_codes=["6010989901"]),
    ],
    "rejected": [
        card("R26BK00000010", is_candidate=False, excluded_reason="제외키워드 (유지보수)", product_codes=["6010989901"]),
        card("R26BK00000011", is_candidate=False, excluded_reason="최소예산 (50,000,000원 < 100,000,000원)", product_codes=["6010989901"]),
        card("R26BK00000012", is_candidate=False, excluded_reason="미매칭 (키워드·품명코드 모두 불일치)"),
        card("R26BK00000013", is_candidate=False, excluded_reason="지역제한 (경기만 참가 — 지일 강원특별자치도 정선군, 근거: 나라장터 참가가능지역)"),
        card("R26BK00000014", is_candidate=False, excluded_reason="마감 지남 (자격등록 마감 10/01 18:00)"),
    ],
    "stats": {"fetched": 120, "prespec_fetched": 20, "period_begin": "2026-10-12T09:00", "period_end": "2026-10-19T09:00"},
}


class TestView(unittest.TestCase):
    def test_stage_names(self):
        self.assertEqual(mview.stage_of("제외키워드 (운영 용역)"), ("제외키워드", "제목에 제외 키워드 '운영 용역'"))
        self.assertEqual(mview.stage_of("보유면허없음 (요구 면허 조경공사업 — …)")[0], "참가자격")
        self.assertEqual(mview.stage_of("지역제한 (경기만 참가)")[0], "참가지역")
        self.assertEqual(mview.stage_of("미매칭 (키워드·품명코드 모두 불일치)")[0], "키워드·품목")

    def test_notices_split(self):
        data = mview.notices(RUN, {}, HELD)
        self.assertEqual([i["noticeNo"] for i in data["items"]], ["R26BK00000001", "R26BK00000003"])
        self.assertEqual([i["noticeNo"] for i in data["titleExcludedItems"]], ["R26BK00000002"])
        self.assertEqual(data["titleExcludedItems"][0]["excludeKeyword"], "운영 용역")
        # 세부품명 공고 탭(목록 밖) — 제외 키워드로 빠진 보유 품목 공고 + 검토필요의 보유 품목 공고. 금액 미달은 뺀다
        held = {i["noticeNo"]: i for i in data["heldItems"]}
        self.assertEqual(set(held), {"R26BK00000010", "R26BK00000002"})
        self.assertEqual(held["R26BK00000010"]["matchedBy"]["products"], [{"code": "6010989901", "name": "실물모형및전시물"}])
        self.assertTrue(held["R26BK00000010"]["outside"])
        item = data["items"][0]
        self.assertEqual(item["deadlineKind"], "자격등록 마감")
        self.assertEqual(item["bidDeadline"], "2099-01-03T10:00")
        self.assertEqual(item["bidMethodCategory"], "협상")
        self.assertEqual(item["basis"], "제목+과업내용")
        self.assertEqual(data["collected"], {"bid": 100, "pre": 20})
        self.assertEqual(data["thresholds"]["high"], 0.65)

    def test_promoted_moves_into_list(self):
        data = mview.notices(RUN, {"R26BK00000010": {"by": "김", "at": "x", "title": "", "keyword": "유지보수"},
                                   "R26BK00000002": {"by": "김", "at": "x", "title": "", "keyword": "운영 용역"}}, HELD)
        listed = {i["noticeNo"]: i for i in data["items"]}
        self.assertIn("R26BK00000010", listed)
        self.assertEqual(listed["R26BK00000010"]["promoted"]["by"], "김")
        self.assertIn("R26BK00000002", listed, "올린 검토필요 공고도 목록으로")
        self.assertEqual(data["titleExcludedItems"], [])

    def test_excluded_hides_no_match_and_expired(self):
        data = mview.excluded(RUN, {}, HELD)
        self.assertEqual({i["excludedAt"] for i in data["items"]}, {"제외키워드", "최소예산", "참가지역"})
        kw = next(i for i in data["items"] if i["excludedAt"] == "제외키워드")
        self.assertEqual(kw["excludedDetail"], "제목에 제외 키워드 '유지보수'")
        steps = {s["step"]: s for s in kw["steps"]}
        self.assertTrue(steps["키워드·품목"]["ok"])
        self.assertIn("[공고 제목] '과학관'", steps["키워드·품목"]["detail"])
        region = next(i for i in data["items"] if i["excludedAt"] == "참가지역")
        self.assertFalse({s["step"]: s for s in region["steps"]}["참가지역"]["ok"])
        self.assertIn("error", mview.excluded(None, {}, HELD))

    def test_excluded_shows_keyword_only_miss(self):
        """키워드·세부품명 하나만 안 맞고 다른 조건은 통과한 공고(only_keyword_miss)는 '세부품명 미달'로 싣는다
        (2026-10-20 팀원 레포 10/19 이식). 다른 조건에도 걸린 미매칭은 그대로 숨긴다."""
        run = {**RUN, "rejected": RUN["rejected"] + [
            card("R26BK00000020", is_candidate=False, excluded_reason="미매칭 (키워드·품명코드 모두 불일치)",
                 only_keyword_miss=True, work_type="용역", product_name="공간정보DB구축서비스", matched_keywords=[]),
        ]}
        data = mview.excluded(run, {}, HELD)
        low = [i for i in data["items"] if i["excludedAt"] == "키워드·품목"]
        self.assertEqual([i["noticeNo"] for i in low], ["R26BK00000020"], "R26BK00000012(플래그 없음)는 숨김")
        self.assertEqual(data["byStage"]["키워드·품목"], 1)
        step = {s["step"]: s for s in low[0]["steps"]}["키워드·품목"]
        self.assertFalse(step["ok"])
        self.assertIn("용역분류(공간정보DB구축서비스)", step["detail"])

    def test_search_by_notice_number(self):
        past = FakePast()
        found = mview.search("R26BK00000012", RUN, past, HELD)
        self.assertEqual(found["notice"]["excludedAt"], "키워드·품목", "미매칭 공고도 공고번호로는 찾는다")
        self.assertEqual(mview.search("R26BK09999999", RUN, past, HELD)["noticeMissing"], "notFound")
        self.assertEqual(mview.search("R26BK09999999", None, past, HELD)["noticeMissing"], "notLoaded")
        self.assertEqual(mview.search("과학관 전시", RUN, past, HELD)["top"][0]["name"], "○○과학관 전시")


class TestTeamStore(unittest.TestCase):
    def setUp(self):
        self.store = TeamStore(Path(tempfile.mkdtemp()))

    def test_decision_owner_undo(self):
        p = self.store.update({"noticeNo": "A", "title": "공고 A", "by": "김", "status": "불참", "tags": ["금액 적음"]})
        self.assertEqual((p["status"], p["owner"], p["tags"], p["decidedBy"]), ("불참", "김", ["금액 적음"], "김"))
        p = self.store.update({"noticeNo": "A", "by": "김", "reason": "1억 미만"})
        self.assertEqual((p["status"], p["reason"]), ("불참", "1억 미만"), "사유만 고치기")
        p = self.store.update({"noticeNo": "A", "by": "이", "status": "참가"})
        self.assertEqual((p["owner"], p["prev"]["status"]), ("이", "불참"))
        p = self.store.update({"noticeNo": "A", "by": "이", "undo": True})
        self.assertEqual((p["status"], p["owner"]), ("불참", "김"))
        p = self.store.update({"noticeNo": "A", "by": "김", "status": ""})
        self.assertEqual((p["status"], p["owner"]), (None, ""), "해제하면 담당도 지움")
        with self.assertRaises(ValueError):
            self.store.update({"noticeNo": "A", "by": "", "status": "참가"})
        with self.assertRaises(ValueError):
            self.store.update({"noticeNo": "A", "by": "김", "status": "모름"})
        saved = json.loads(self.store.part_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["A"]["title"], "공고 A")

    def test_comments_only_author_edits(self):
        p = self.store.update({"noticeNo": "A", "by": "김", "comment": "검토 부탁"})
        at = p["comments"][0]["at"]
        with self.assertRaises(ValueError):
            self.store.update({"noticeNo": "A", "by": "이", "editComment": {"at": at, "text": "바꿈"}})
        p = self.store.update({"noticeNo": "A", "by": "김", "editComment": {"at": at, "text": "바꿈"}})
        self.assertEqual(p["comments"][0]["text"], "바꿈")
        p = self.store.update({"noticeNo": "A", "by": "김", "deleteComment": {"at": at}})
        self.assertEqual(p["comments"], [])

    def test_promote(self):
        self.store.promote({"noticeNo": "A", "by": "김", "on": True, "keyword": "운영 용역"})
        self.assertEqual(self.store.promoted["A"]["keyword"], "운영 용역")
        self.store.promote({"noticeNo": "A", "by": "김", "on": False})
        self.assertNotIn("A", self.store.promoted)

    def test_migrates_old_sqlite_once(self):
        folder = Path(tempfile.mkdtemp())
        db = folder / "webapp.sqlite3"
        Store(db)  # 예전 화면 표 만들기
        con = sqlite3.connect(db)
        con.execute("INSERT INTO notice_state(key, status, assignee, updated_by, updated_at, promoted_by, promoted_at) "
                    "VALUES ('R1-000','보류','김','김','2026-10-13T10:00:00','이','2026-10-13T11:00:00')")
        con.execute("INSERT INTO comments(key, author, body, created_at) VALUES ('R1-000','김','공동수급 확인','2026-10-13T10:01:00')")
        con.execute("INSERT INTO decisions(key, status, reasons, memo, decided_by, decided_at, snapshot) VALUES "
                    "('R1-001','보류','[\"공고문 확인 필요\"]','메모','김','2026-10-14T09:00:00','{\"title\": \"공고 R1\"}')")
        con.commit()
        con.close()
        team = TeamStore(folder, legacy_db=db)
        p = team.participation["R1"]
        self.assertEqual((p["status"], p["owner"], p["tags"], p["reason"], p["title"]), ("보류", "김", ["공고문 확인 필요"], "메모", "공고 R1"))
        self.assertEqual(p["comments"][0]["text"], "공동수급 확인")
        self.assertEqual(team.promoted["R1"]["by"], "이")
        team.update({"noticeNo": "R1", "by": "김", "status": "참가"})
        again = TeamStore(folder, legacy_db=db)
        self.assertEqual(again.participation["R1"]["status"], "참가", "두 번째부터는 json 파일이 기준")


class TestServerRoutes(unittest.TestCase):
    def setUp(self):
        store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")
        past = FakePast()
        self.collector = Collector(store, past, runner=fixture_run, config_loader=config_with_key)
        self.app = App(store, past, self.collector, token="secret-token")
        self.app._held_raw = HELD
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path, cookie=None):
        req = urllib.request.Request(self.base + path, headers={"Cookie": cookie} if cookie else {})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.headers, res.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def post(self, path, body, cookie="app_token=secret-token"):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Cookie": cookie})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_cookie_from_token_link(self):
        status, headers, _ = self.get("/?t=secret-token")
        self.assertEqual(status, 200)
        self.assertIn("app_token=secret-token", headers.get("Set-Cookie"))
        self.assertIn("HttpOnly", headers.get("Set-Cookie"))
        self.assertIsNone(self.get("/")[1].get("Set-Cookie"), "토큰 없는 주소엔 쿠키 안 줌")
        status, _, body = self.get("/api/notices")
        self.assertEqual(status, 401)
        self.assertTrue(json.loads(body)["unauthorized"])
        status, _, body = self.get("/api/notices", cookie="app_token=secret-token")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["empty"], "아직 수집 전")

    def test_refresh_collects_then_lists(self):
        status, _, body = self.get("/api/notices?days=7&refresh=1", cookie="app_token=secret-token")
        self.assertTrue(json.loads(body)["running"])
        for _ in range(100):
            _, _, raw = self.get("/api/job", cookie="app_token=secret-token")
            if not json.loads(raw)["running"]:
                break
            time.sleep(0.05)
        _, _, raw = self.get("/api/notices", cookie="app_token=secret-token")
        data = json.loads(raw)
        self.assertGreater(len(data["items"]), 0)
        self.assertEqual(data["format"], data["resultFormat"])
        no = data["items"][0]["noticeNo"]
        status, res = self.post("/api/participation", {"noticeNo": no, "title": "t", "by": "김", "status": "참가"})
        self.assertEqual((status, res["participation"]["status"]), (200, "참가"))
        self.assertEqual(self.post("/api/participation", {"noticeNo": no, "status": "참가"})[0], 400, "이름 없이 거부")
        _, _, raw = self.get("/api/participation", cookie="app_token=secret-token")
        self.assertEqual(json.loads(raw)["participation"][no]["owner"], "김")
        status, res = self.post("/api/promote", {"noticeNo": "X1", "by": "김", "on": True})
        self.assertIn("X1", res["promoted"])
        for path in ("/api/excluded", "/api/enrich", "/api/corpus", "/api/search?q=%EA%B3%BC%ED%95%99%EA%B4%80", "/api/project?id=0"):
            status, _, raw = self.get(path, cookie="app_token=secret-token")
            self.assertEqual(status, 200, path)
            json.loads(raw)


if __name__ == "__main__":
    unittest.main()
