"""사내 웹앱(webapp/) 테스트 — 수집은 픽스처로 대신하고, API·상태 저장·직렬화를 확인한다.

jiil-past-contracts(비공개)가 옆에 없으면 싱크로율 실계산 테스트만 건너뛴다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import qualify  # noqa: E402
from nego.config import load_config  # noqa: E402
from nego.models import notice_from_raw  # noqa: E402
from nego.pipeline import RunStats, build_candidates  # noqa: E402
from tests import fixtures  # noqa: E402
from webapp.collect import Collector, serialize  # noqa: E402
from webapp.server import App, make_handler  # noqa: E402
from webapp.store import Store  # noqa: E402
from webapp.sync import PastIndex, default_jiil_repo, level  # noqa: E402

NOW = datetime(2026, 9, 14, 9, 0)


class FakePast:
    """과거 실적 없이도 돌도록 점수를 고정으로 돌려주는 대역."""

    error = None
    projects = [{"year": 2025, "title": "○○과학관 전시", "overview": "전시물 제작", "exhibition": "", "tags": {}}]

    def score(self, title, text="", top=5):
        return {"score": 0.7, "level": level(0.7), "basis": "과업 원문" if text else "공고명만",
                "tags": {}, "top": [{"index": 0, "year": 2025, "title": "○○과학관 전시", "score": 0.7, "shared": []}]}

    def public_list(self):
        return self.projects


RUN_PERIODS = []


def fixture_run(config, days, attachments, cats, prespec=False, period=None):
    RUN_PERIODS.append(period)
    config.screen.keywords = ["전시관", "박물관", "과학관", "체험관", "전시디자인", "전시물"]
    stats = RunStats()
    notices = [notice_from_raw(raw, "용역") for raw in fixtures.service_notices()]
    groups = qualify.group_license_rows(fixtures.license_rows())
    return build_candidates(notices, config, groups, {}, NOW, stats, cats), stats


def config_with_key():
    config = load_config()
    config.api.service_key = "TESTKEY-0123456789"
    return config


class TestFormatInSync(unittest.TestCase):
    def test_app_js_expects_current_result_format(self):
        """화면(app.js APP_FORMAT)과 서버(RESULT_FORMAT)가 같은 형식 번호여야 "서버 재시작 필요" 경고가 맞게 뜬다."""
        import re
        from webapp.collect import RESULT_FORMAT

        js = (Path(__file__).resolve().parent.parent / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertEqual(int(re.search(r"const APP_FORMAT = (\d+);", js).group(1)), RESULT_FORMAT)


class TestSyncLevel(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(level(None), "판정 불가")
        self.assertEqual(level(0.8), "높음")
        self.assertEqual(level(0.5), "경계선")
        self.assertEqual(level(0.1), "낮음")

    def test_missing_repo_degrades(self):
        past = PastIndex.load(Path(tempfile.mkdtemp()))
        self.assertFalse(past.available)
        self.assertIn("JIIL_REPO", past.error)
        self.assertEqual(past.score("박물관 전시")["level"], "판정 불가")

    @unittest.skipUnless((default_jiil_repo() / "docs/summaries/past_task_profiles.json").exists(), "jiil 레포 없음")
    def test_real_profiles_rank_similar_project(self):
        past = PastIndex.load()
        museum = past.score("어린이박물관 전시 설계 및 제작설치", "어린이박물관 체험 전시물\n어린이박물관 모형 제작\n전시 그래픽")
        lighting = past.score("공원 야간경관 조명 설치", "경관조명 기구 설치\n연출제어시스템")
        self.assertGreater(museum["score"], 0)
        self.assertTrue(museum["top"])
        self.assertEqual(museum["basis"], "과업 원문")
        self.assertNotEqual(museum["top"][0]["title"], lighting["top"][0]["title"])


class TestSerialize(unittest.TestCase):
    def test_candidate_card_has_qualification_and_sync(self):
        candidates, stats = fixture_run(load_config(), 7, False, {"협상"})
        cards = {c["notice_no"]: c for c in (serialize(x, FakePast()) for x in candidates)}
        flagged = cards["R26TEST00009"]
        self.assertTrue(flagged["qualification"]["checked"])
        self.assertFalse(flagged["qualification"]["passes"])
        self.assertTrue(flagged["qualification"]["missing"])
        q = flagged["qualification"]
        self.assertEqual([p["key"] for p in q["parts"]], ["industry", "product"])
        self.assertEqual(q["parts"][0]["status"], "미달")
        self.assertEqual(len(q["parts"][0]["missing"]), 2)
        # 기계설비공사업 행은 전기공사업을 대신 인정하지만(판정에만 씀) 화면엔 대표 면허만 나온다
        self.assertIn("기계설비공사업(0009)", q["missing"])
        self.assertFalse(any("전기공사업" in line for line in q["missing"]), q["missing"])
        self.assertIn("g2b.go.kr", flagged["detail_url"])
        self.assertEqual(flagged["category"], "협상")
        self.assertEqual(flagged["key"], "R26TEST00009-000")
        self.assertEqual(flagged["sync"]["basis"], "공고명만")
        json.dumps(flagged, ensure_ascii=False)  # 화면으로 보낼 수 있어야 한다
        rejected = [serialize(x, FakePast()) for x in stats.rejected]
        self.assertTrue(all(r["excluded_reason"] for r in rejected))


class TestQualificationView(unittest.TestCase):
    """2026-09-29 사용자 지적 3건을 그대로 재현한다."""

    def test_need_counts_minimum_licenses_to_acquire(self):
        """'최소 N개 부족' — 반드시 1 + 택1 1 = 2, 조합 요건은 가장 덜 모자란 조합의 미보유 수."""
        from nego import qualify
        from webapp import qualview
        waste = [{"bidNtceNo": "W", "lmtGrpNo": str(g), "lmtSno": str(n), "lcnsLmtNm": name}
                 for g, pair in enumerate([("A/1227", "B/6786"), ("A/1227", "C/1143")], 1) for n, name in enumerate(pair, 1)]
        q = qualify.evaluate(qualify.group_license_rows(waste)["W"], [])
        self.assertEqual(qualview.build(q, (set(), set()), {"industry": True})[0]["need"], 2)
        combo = [{"bidNtceNo": "C", "lmtGrpNo": str(g), "lmtSno": str(n), "lcnsLmtNm": name}
                 for g, pair in enumerate([("A/0001", "B/0002"), ("C/0003", "D/0004")], 1) for n, name in enumerate(pair, 1)]
        q = qualify.evaluate(qualify.group_license_rows(combo)["C"], [], {"0003"})
        self.assertEqual(qualview.build(q, ({"0003"}, set()), {"industry": True})[0]["need"], 1, "C 보유 → D 하나만 더")

    def test_substitute_shown_only_when_notice_says_or(self):
        """공고문이 "토목공사업 또는 토목건축공사업"이라 적었으면 대신 인정 업종도 보여주고, 아니면 숨긴다."""
        from nego import qualify
        from webapp import qualview
        rows = [
            {"bidNtceNo": "N", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "토목공사업/0001",
             "permsnIndstrytyList": "[토목공사업/0001][토목건축공사업/0003]"},
            {"bidNtceNo": "N", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "상·하수도설비공사업/4996"},
            {"bidNtceNo": "N", "lmtGrpNo": "2", "lmtSno": "2", "lcnsLmtNm": "지반조성·포장공사업/4989"},
        ]
        q = qualify.evaluate(qualify.group_license_rows(rows)["N"], [])
        doc = "1) 토목공사업 또는 토목건축공사업을 등록한 자\n2) 지반조성·포장공사업과 상하수도설비공사업을 모두 등록한자"
        combo = qualview.build(q, (set(), set()), {"industry": True}, doc_text=doc)[0]["missing"][0]["combos"]
        self.assertEqual(combo[0]["rows"][0]["label"], "토목공사업(0001) 또는 토목건축공사업")
        combo = qualview.build(q, (set(), set()), {"industry": True})[0]["missing"][0]["combos"]
        self.assertEqual(combo[0]["rows"][0]["label"], "토목공사업(0001)", "공고문이 없으면 숨김")
        # 공고문에 이름만 있고 "또는"으로 이어지지 않으면 숨김 (폐기물: "① 1227 ② 종합처분업")
        waste = [{"bidNtceNo": "W", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "폐기물수집·운반업/1227",
                  "permsnIndstrytyList": "[폐기물수집·운반업/1227][폐기물종합처분업]"}]
        q = qualify.evaluate(qualify.group_license_rows(waste)["W"], [])
        doc = "① 폐기물수집·운반업(업종코드:1227)\n② 폐기물종합처분업(업종코드:1143)"
        req = qualview.build(q, (set(), set()), {"industry": True}, doc_text=doc)[0]["missing"][0]
        self.assertFalse(req["any_of"])
        self.assertEqual([i["label"] for i in req["items"]], ["폐기물수집·운반업(1227)"])

    def test_substitute_chain_of_or_all_shown(self):
        """"A[4440] 또는 B[4442] 또는 C[4443] 또는 D[4444]" — 사슬로 이어진 대체 업종 모두 표시 (국립울진해양과학관)."""
        from nego import qualify
        from webapp import qualview
        rows = [{"bidNtceNo": "U", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "산업디자인전문회사(시각디자인분야)/4440",
                 "permsnIndstrytyList": "[산업디자인전문회사(시각디자인분야)/4440][산업디자인전문회사(환경디자인분야)/4442]"
                                        "[산업디자인전문회사(멀티미디어디자인분야)/4443][산업디자인전문회사(종합디자인분야)/4444]"}]
        held_raw = {"heldIndustries": [
            {"code": "4440", "name": "산업디자인전문회사(시각디자인분야)"},
            {"code": "4442", "name": "산업디자인전문회사(환경디자인분야)"},
            {"code": "4444", "name": "산업디자인전문회사(종합디자인분야)"},
        ]}
        q = qualify.evaluate(qualify.group_license_rows(rows)["U"], qualify.load_held_names(held_raw),
                             qualify.load_held_codes(held_raw))
        doc =("⑥ 「산업디자인진흥법」 제9조에 의한 산업디자인전문회사(시각디자인분야)[업종코드 4440] 또는 산업\n"
               "디자인전문회사(환경디자인분야)[업종코드 4442] 또는 산업디자인전문회사(멀티미디어디자인분야)\n"
               "[업종코드 4443] 또는 산업디자인전문회사(종합디자인분야)[업종코드 4444]로 입찰참가 등록 한 자")
        part = qualview.build(q, qualview.held_lookup(held_raw), {"industry": True}, doc_text=doc)[0]
        req = part["satisfied"][0]
        self.assertTrue(req["any_of"])
        self.assertEqual(len(req["items"]), 4, [i["label"] for i in req["items"]])
        self.assertEqual([i["held"] for i in req["items"]], [True, True, False, True])

    def test_different_fields_of_same_license_are_not_merged(self):
        """"산업디자인전문회사(시각디자인분야)"와 "(환경디자인분야)"는 다른 업종 — 괄호 앞 이름이 같다고 합치면 안 된다."""
        from nego import qualify
        from webapp import qualview
        rows = [
            {"bidNtceNo": "D", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "산업디자인전문회사(환경디자인분야)/4442"},
            {"bidNtceNo": "D", "lmtGrpNo": "2", "lmtSno": "1", "lcnsLmtNm": "산업디자인전문회사(종합디자인분야)/4444"},
        ]
        q = qualify.evaluate(qualify.group_license_rows(rows)["D"], [])
        req = qualview.build(q, (set(), set()), {"industry": True})[0]["missing"][0]
        self.assertEqual([i["label"] for i in req["items"]],
                         ["산업디자인전문회사(환경디자인분야)(4442)", "산업디자인전문회사(종합디자인분야)(4444)"])

    def test_substitute_license_used_for_judgment_but_not_shown(self):
        """허용업종(대신 인정)은 판정에만 쓰고, 그걸로 채웠을 때만 '(대체 인정)'으로 적는다."""
        from nego import qualify
        from webapp import qualview
        rows = [
            {"bidNtceNo": "B", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "건축공사업/0002",
             "permsnIndstrytyList": "[건축공사업/0002][토목건축공사업]"},
        ]
        groups = qualify.group_license_rows(rows)["B"]
        held = (set(), {"토목건축공사업"})
        ok = qualify.evaluate(groups, ["토목건축공사업"])
        self.assertEqual(ok.missing_count, 0)
        part = qualview.build(ok, held, {"industry": True, "product": False})[0]
        self.assertEqual(part["held"], ["토목건축공사업 (대체 인정)"])
        miss = qualify.evaluate(groups, [])
        part = qualview.build(miss, (set(), set()), {"industry": True, "product": False})[0]
        self.assertEqual([r["items"][0]["label"] for r in part["missing"]], ["건축공사업(0002)"])
        self.assertNotIn("alts", part["missing"][0])

    def test_attachment_duplicate_of_api_license_shown_once(self):
        """2026-10-01 고이분교: 공고문 "건축공사업(또는 토목·건축공사업)을 등록한 자"를 첨부 판정이 또 잡아
        건축공사업이 두 번 나왔고, 가운뎃점 때문에 토목건축공사업이 안 보였다."""
        from nego import qualify
        from webapp import qualview
        rows = [{"bidNtceNo": "B", "lmtGrpNo": "1", "lmtSno": "1", "lcnsLmtNm": "건축공사업/0002",
                 "permsnIndstrytyList": "[토목건축공사업/0003]"}]
        api = qualify.evaluate(qualify.group_license_rows(rows)["B"], [])
        items = ["가. 종합건설업종 중 건축공사업(또는 토목·건축공사업)을 등록한 자",
                 "사. 적격심사 평가대상 업종 및 평가비율\n종합 건축공사업 680,362,000 100%"]
        att = qualify.evaluate_attachment_text(items, set(), {}, {"0002": "건축공사업", "0003": "토목건축공사업"})
        self.assertTrue(att.checked)
        merged = qualify.merge_results(api, att)
        part = qualview.build(merged, (set(), set()), {"industry": True}, doc_text="\n".join(items))[0]
        self.assertEqual([[i["label"] for i in r["items"]] for r in part["missing"]],
                         [["건축공사업(0002)", "토목건축공사업"]])
        # API가 허용업종을 안 줘도 건축공사업은 한 번만
        rows[0]["permsnIndstrytyList"] = ""
        api = qualify.evaluate(qualify.group_license_rows(rows)["B"], [])
        part = qualview.build(qualify.merge_results(api, att), (set(), set()), {"industry": True})[0]
        self.assertEqual([[i["label"] for i in r["items"]] for r in part["missing"]], [["건축공사업(0002)"]])

    HELD = ({"1469", "4440", "4442", "4444", "4990", "6010989901"}, set())

    def _q(self, missing=(), satisfied=()):
        from nego.qualify import LicenseGroup, QualificationResult

        mk = lambda names: [LicenseGroup(str(i), list(n)) for i, n in enumerate(names)]
        m, sat = mk(missing), mk(satisfied)
        return QualificationResult(total_groups=len(m) + len(sat), missing_groups=m, passes=not m, checked=True,
                                   satisfied_groups=sat)

    def test_same_held_qualification_listed_once(self):
        from webapp import qualview

        sw = "소프트웨어사업자(디지털콘텐츠개발서비스사업)(1469)"
        q = self._q(satisfied=[[sw, "산업디자인전문회사(환경디자인분야)(4442)"],
                               [sw, "산업디자인전문회사(종합디자인분야)(4444)"]])
        ind = qualview.build(q, self.HELD, {"industry": True, "product": True})[0]
        self.assertEqual(ind["status"], "충족")
        self.assertEqual(ind["held"].count(sw), 1, "1469가 두 요건을 채워도 한 번만")

    def test_mixed_any_of_requirement_goes_to_industry(self):
        from webapp import qualview

        q = self._q(satisfied=[["실물모형및전시물(6010989901)", "실내건축공사업(4990)",
                                "소프트웨어사업자(디지털콘텐츠개발서비스사업)(1469)", "산업디자인전문회사(시각디자인분야)(4440)"]])
        ind, prod = qualview.build(q, self.HELD, {"industry": True, "product": True})
        # 사용자 지적(2026-09-29 울산박물관): 10자리 품목이 자격요건 칩에 뜨고 세부품명번호는 "제한 없음"이던 문제
        self.assertEqual(ind["status"], "충족")
        self.assertEqual(len(ind["held"]), 3)
        self.assertEqual(prod["status"], "충족")
        self.assertEqual(prod["held"], ["실물모형및전시물(6010989901)"])

    def test_both_chips_always_present(self):
        from webapp import qualview

        q = self._q(missing=[["조명용제어장치(3912110702)"]])
        ind, prod = qualview.build(q, self.HELD, {"industry": True, "product": True})
        self.assertEqual((ind["status"], prod["status"]), ("제한 없음", "미달"))
        self.assertEqual(len(prod["missing"]), 1)
        ind, prod = qualview.build(self._q(), self.HELD, {"industry": False, "product": False})
        self.assertEqual((ind["status"], prod["status"]), ("미확인", "미확인"))

    def test_missing_counts_requirements_and_marks_held(self):
        from webapp import qualview

        q = self._q(missing=[["토목공사업(0001)", "토목건축공사업"], ["상·하수도설비공사업(4996)", "지반조성·포장공사업(4989)"]])
        ind = qualview.build(q, self.HELD, {"industry": True, "product": False})[0]
        self.assertEqual((ind["status"], len(ind["missing"])), ("미달", 2))
        self.assertTrue(all(r["any_of"] for r in ind["missing"]))
        self.assertFalse(any(i["held"] for r in ind["missing"] for i in r["items"]))

    def test_same_code_with_different_labels_counted_once(self):
        # 2026-09-30 제보: "G2B분류번호 교육훈련장비(6010999901)"와 "육훈련장비(6010999901)"가 미보유 2건으로
        from webapp import qualview

        q = self._q(missing=[["G2B분류번호 교육훈련장비(6010999901)"], ["육훈련장비(6010999901)"]])
        prod = qualview.build(q, self.HELD, {"industry": True, "product": True})[1]
        self.assertEqual(len(prod["missing"]), 1)
        self.assertEqual(prod["missing"][0]["items"][0]["label"], "교육훈련장비(6010999901)")


class _Url(unittest.TestCase):
    def test_detail_url_fallback(self):
        from nego.models import prespec_from_raw
        from webapp.collect import _g2b_url

        n = notice_from_raw(fixtures.notice("R26BK01745222", bidNtceDtlUrl=""), "공사")
        self.assertEqual(_g2b_url(n), "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK01745222&bidPbancOrd=000")
        self.assertIsNone(_g2b_url(prespec_from_raw({"bfSpecRgstNo": "R26BD1"}, "물품")))


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")

    def test_state_and_comments(self):
        self.store.set_state("A-000", by="김", status="검토중")
        self.store.set_state("A-000", by="이", assignee="이")
        st = self.store.states()["A-000"]
        self.assertEqual((st["status"], st["assignee"], st["updated_by"]), ("검토중", "이", "이"))
        self.store.set_state("A-000", by="이", clear_assignee=True)
        self.assertIsNone(self.store.states()["A-000"]["assignee"])
        with self.assertRaises(ValueError):
            self.store.set_state("A-000", by="김", status="아무거나")
        self.store.add_comment("A-000", "김", "공동수급 가능?")
        self.assertEqual(self.store.comment_counts(), {"A-000": 1})


class TestServer(unittest.TestCase):
    def setUp(self):
        store = Store(Path(tempfile.mkdtemp()) / "t.sqlite3")
        past = FakePast()
        self.collector = Collector(store, past, runner=fixture_run, config_loader=config_with_key)
        app = App(store, past, self.collector, token="secret-token")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, body=None, token="secret-token"):
        req = urllib.request.Request(self.base + path, headers={"X-App-Token": token})
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_token_required_for_api_not_for_page(self):
        self.assertEqual(self.call("/api/meta", token="wrong")[0], 401)
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn("싱크로율 탐색기", res.read().decode())
        self.assertEqual(self.call("/../server.py")[0], 404)

    def test_collect_then_state_and_comments(self):
        status, _ = self.call("/api/collect", {"days": 7, "attachments": False, "categories": ["협상"]})
        self.assertEqual(status, 202)
        for _ in range(100):
            job = self.call("/api/job")[1]
            if not job["running"]:
                break
            time.sleep(0.05)
        self.assertIsNone(job["error"])
        run = self.call("/api/results")[1]["run"]
        keys = [c["key"] for c in run["candidates"]]
        self.assertIn("R26TEST00009-000", keys)
        self.assertTrue(run["rejected"])

        self.assertEqual(self.call("/api/state", {"key": keys[0], "status": "참가"})[0], 400, "이름 없이 거부")
        status, body = self.call("/api/state", {"key": keys[0], "status": "참가", "take": True, "name": "김지일"})
        self.assertEqual((status, body["state"]["status"], body["state"]["assignee"]), (200, "참가", "김지일"))
        _, body = self.call("/api/comments", {"key": keys[0], "name": "김지일", "body": "검토 부탁"})
        self.assertEqual(body["comments"][0]["body"], "검토 부탁")
        res = self.call("/api/results")[1]
        self.assertEqual(res["comment_counts"][keys[0]], 1)
        self.assertEqual(res["states"][keys[0]]["status"], "참가")

    def test_custom_period(self):
        """직접 지정 구간: 시작일 00:00 ~ 종료일 23:59(오늘이면 지금까지). 잘못된 구간은 400."""
        from datetime import date, timedelta as td
        from webapp.collect import custom_period

        now = datetime(2026, 10, 1, 14, 30)
        self.assertEqual(custom_period("2026-09-01", "2026-09-30", now),
                         (datetime(2026, 9, 1), datetime(2026, 9, 30, 23, 59)))
        self.assertEqual(custom_period("2026-09-25", "2026-10-01", now)[1], now)
        for bad in (("2026-09-30", "2026-09-01"), ("2026-06-01", "2026-09-30"), ("2026/09/01", "2026-09-30")):
            with self.assertRaises(ValueError):
                custom_period(*bad, now)
        self.assertEqual(self.call("/api/collect", {"begin": "2026-09-30", "end": "2026-09-01"})[0], 400)

        begin = (date.today() - td(days=10)).isoformat()
        end = (date.today() - td(days=3)).isoformat()
        RUN_PERIODS.clear()
        status, job = self.call("/api/collect", {"begin": begin, "end": end, "attachments": False})
        self.assertEqual((status, job["params"]["begin"], job["params"]["end"], job["params"]["days"]), (202, begin, end, None))
        for _ in range(100):
            job = self.call("/api/job")[1]
            if not job["running"]:
                break
            time.sleep(0.05)
        self.assertIsNone(job["error"])
        self.assertEqual([(p[0].date().isoformat(), p[1].date().isoformat()) for p in RUN_PERIODS], [(begin, end)])

    def _collect_and_wait(self, body):
        status, job = self.call("/api/collect", body)
        self.assertEqual(status, 202)
        for _ in range(100):
            job = self.call("/api/job")[1]
            if not job["running"]:
                break
            time.sleep(0.05)
        self.assertIsNone(job["error"])
        return job

    def test_viewer_keeps_own_run(self):
        """내가 불러온 결과(?run=번호)는 다른 팀원이 나중에 불러오기를 해도 그대로 받을 수 있다. 지워진 번호면 최신."""
        mine = self._collect_and_wait({"days": 7, "attachments": False})
        other = self._collect_and_wait({"days": 1, "attachments": False})
        self.assertNotEqual(mine["run_id"], other["run_id"])
        self.assertEqual(self.call("/api/results")[1]["run"]["id"], other["run_id"], "번호 없으면 최신")
        res = self.call(f"/api/results?run={mine['run_id']}")[1]
        self.assertEqual((res["run"]["id"], res["run"]["params"]["days"], res["missing"]), (mine["run_id"], 7, False))
        gone = self.call("/api/results?run=999999")[1]
        self.assertEqual((gone["run"]["id"], gone["missing"]), (other["run_id"], True))
        # 1분마다 하는 새로 조회 — 들고 있는 결과(have)와 같으면 본문을 안 보내고, 다르면 보낸다
        same = self.call(f"/api/results?run={mine['run_id']}&have={mine['run_id']}")[1]
        self.assertEqual((same["run"], same.get("same")), (None, True))
        self.assertIn("states", same)
        latest = self.call(f"/api/results?have={other['run_id']}")[1]
        self.assertEqual((latest["run"], latest.get("same")), (None, True))
        changed = self.call(f"/api/results?have={mine['run_id']}")[1]
        self.assertEqual((changed["run"]["id"], changed.get("same")), (other["run_id"], None), "최신이 바뀌었으면 본문")
        gone2 = self.call("/api/results?run=999999&have=999999")[1]
        self.assertEqual((gone2["run"]["id"], gone2["missing"]), (other["run_id"], True))

    def test_old_runs_are_pruned(self):
        from webapp.store import Store

        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "s.sqlite3")
            st.KEEP_RUNS = 3
            ids = [st.save_run("2026-10-01T10:00:00", {"days": 1}, {"candidates": [], "rejected": [], "stats": {}})
                   for _ in range(5)]
            self.assertIsNone(st.run(ids[1]))
            self.assertEqual([st.run(i)["id"] for i in ids[-3:]], ids[-3:])

    def test_bad_period_and_missing_key(self):
        self.assertEqual(self.call("/api/collect", {"days": 5})[0], 400)
        self.collector._config_loader = load_config  # 서비스키 없는 설정
        import os
        saved = os.environ.pop("NARA_SERVICE_KEY", None)
        try:
            self.call("/api/collect", {"days": 1, "attachments": False})
            for _ in range(100):
                job = self.call("/api/job")[1]
                if not job["running"]:
                    break
                time.sleep(0.05)
            self.assertIn("NARA_SERVICE_KEY", job["error"])
        finally:
            if saved is not None:
                os.environ["NARA_SERVICE_KEY"] = saved


if __name__ == "__main__":
    unittest.main()
