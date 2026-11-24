"""회사 고르기(2026-11-24 사용자 요청: 지일·그리다) — 같은 서버·같은 수집 규칙, 보유 자격 파일만 다르다.
회사마다 수집 결과·판단 기록이 따로이고, 수집은 회사를 통틀어 한 번에 하나."""

from __future__ import annotations

import json
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

from nego.config import list_companies, load_config  # noqa: E402
from tests.test_webapp import FakePast, fixture_run  # noqa: E402
from webapp.collect import Collector  # noqa: E402
from webapp.server import App, make_handler  # noqa: E402
from webapp.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def loader(company):
    def load():
        config = load_config(company=company)
        config.api.service_key = "TESTKEY-0123456789"
        return config
    return load


def test_companies_and_held_files():
    assert [c["id"] for c in list_companies()][:2] == ["jiil", "grida"]
    jiil, grida = load_config(), load_config(company="grida")
    assert jiil.held_raw.get("heldIndustries"), "지일 보유 자격은 예전 파일 그대로"
    assert grida.screen.keywords == jiil.screen.keywords, "키워드는 같다(사용자 결정)"
    assert grida.held_raw is not jiil.held_raw and "heldIndustries" in grida.held_raw
    from nego import qualify, region
    held = qualify.load_held_codes(grida.held_raw)
    assert {"0006", "4444", "3244", "6012100201"} <= held and "0036" not in held, "그리다 등록증 기준(11/24)"
    assert region.company_sido(grida.held_raw) == "경기" and region.company_sido(jiil.held_raw) == "강원"


def test_screen_has_picker():
    html = (ROOT / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="coPick"' in html and 'data-co="jiil"' in html and 'data-co="grida"' in html
    assert "co=" in html and "sessionStorage" in html, "고른 회사를 요청마다 붙이고 이 탭에 기억"


class CompanyServerTest(unittest.TestCase):
    def setUp(self):
        folder = Path(tempfile.mkdtemp())
        store = Store(folder / "webapp.sqlite3")
        past = FakePast()
        self.app = app = App(store, past, Collector(store, past, runner=fixture_run, config_loader=loader("jiil")),
                             token="t")
        grida_store = Store(folder / "grida" / "webapp.sqlite3")
        app.add_company("grida", "그리다", "(주)그리다",
                        collector=Collector(grida_store, past, runner=fixture_run, config_loader=loader("grida")))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, headers={"X-App-Token": "t"})
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def wait(self, co):
        for _ in range(200):
            if not self.app.companies[co].collector.job.running:
                return
            time.sleep(0.05)

    def test_results_and_judgements_are_per_company(self):
        meta = self.call("/api/meta?co=grida")[1]
        self.assertEqual([c["id"] for c in meta["companies"]], ["jiil", "grida"])
        self.assertEqual(meta["company"], "grida")
        self.assertTrue(self.call("/api/notices?refresh=1&days=7")[1]["running"])
        self.wait("jiil")
        self.assertTrue(self.app.store.latest_run(), "지일 결과")
        self.assertIsNone(self.app.companies["grida"].store.latest_run(), "그리다는 아직 안 불러옴")
        self.assertTrue(self.call("/api/notices?co=grida")[1].get("empty"))
        self.call("/api/notices?refresh=1&days=7&co=grida")
        self.wait("grida")
        self.assertTrue(self.app.companies["grida"].store.latest_run())
        # 판단 기록은 회사마다 따로
        self.call("/api/participation?co=grida", {"noticeNo": "R26BK00000001", "status": "참가", "by": "김"})
        self.assertIn("R26BK00000001", self.call("/api/participation?co=grida")[1]["participation"])
        self.assertNotIn("R26BK00000001", self.call("/api/participation")[1]["participation"])
        self.assertEqual(self.app.companies["grida"].team.part_path.parent.name, "grida")

    def test_one_collection_at_a_time_across_companies(self):
        gate = threading.Event()

        def slow(*args, **kwargs):
            gate.wait(5)
            return fixture_run(*args, **kwargs)
        self.app.collector._runner = slow
        self.call("/api/notices?refresh=1&days=7")
        res = self.call("/api/notices?refresh=1&days=7&co=grida")[1]
        self.assertIn("지일 공고를 불러오는 중", res.get("error", ""))
        gate.set()
        self.wait("jiil")


if __name__ == "__main__":
    unittest.main()
