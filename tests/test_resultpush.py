"""서버 PC → NAS 수집 결과 보내기 — 실제 HTTP로 받는 쪽까지, 재시도·보기 전용·겹침 방지."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import requests

from tests.test_webapp import FakePast
from webapp.collect import RESULT_FORMAT, Collector
from webapp.docscan import DocScan
from webapp.resultpush import ResultPush
from webapp.server import App, make_handler
from webapp.store import Store

TOKEN = "secret"
PAYLOAD = {"format": RESULT_FORMAT, "keywords": [], "candidates": [{"notice_no": "R1"}], "rejected": [], "stats": {}}


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.addCleanup(self._close)
        self._servers = []

    def _close(self):
        for httpd in self._servers:
            httpd.shutdown()
            httpd.server_close()
        for app in getattr(self, "_apps", []):
            app.store._db.close()

    def server(self, name: str, viewer: bool):
        folder = self.dir / name
        store = Store(folder / "webapp.sqlite3")
        docscan = DocScan(folder / "doc_products.json")
        app = App(store, FakePast(), Collector(store, FakePast(), docscan=docscan), TOKEN, docscan=docscan)
        app.viewer = viewer
        self._apps = getattr(self, "_apps", []) + [app]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self._servers.append(httpd)
        return app, f"http://127.0.0.1:{httpd.server_address[1]}"

    def pusher(self, url: str, store=None, busy=lambda: False, post=requests.post, token=TOKEN):
        store = store or Store(self.dir / "pc.sqlite3")
        self.addCleanup(store._db.close)
        docs = SimpleNamespace(results={"R9": {"codes": ["123"], "file": "공고.hwp", "at": "2026-11-05T10:00:00"}})
        return ResultPush(url, token, store, docs, busy, self.dir / "result_push.json", post=post), store

    def get(self, url: str, path: str):
        req = urllib.request.Request(url + path, headers={"X-App-Token": TOKEN})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())


class PushTest(_Base):
    def test_push_reaches_viewer_with_doc_products_and_finish_time(self):
        nas, url = self.server("nas", viewer=True)
        push, pc = self.pusher(url)
        pc.save_run("2026-11-05T09:00:00", {"days": 30}, PAYLOAD, finished_at="2026-11-05T10:13:00")

        self.assertTrue(push.step())

        run = nas.store.latest_run()
        self.assertEqual(run["candidates"], [{"notice_no": "R1"}])
        self.assertEqual(run["finished_at"], "2026-11-05T10:13:00")  # 화면 '수집 …' 시각은 NAS에 받은 때가 아니라 수집 끝난 때
        self.assertEqual(run["params"], {"days": 30})
        self.assertNotIn("id", run["params"])
        self.assertEqual(nas.docscan.codes(), {"R9": ["123"]})
        self.assertTrue(nas.imported)
        self.assertIsNone(push.state["error"])

    def test_same_run_is_sent_once_even_after_restart(self):
        calls = []

        def post(*args, **kwargs):
            calls.append(args)
            return SimpleNamespace(status_code=200, text="")

        push, pc = self.pusher("http://nas", post=post)
        pc.save_run("a", {}, PAYLOAD, finished_at="2026-11-05T10:00:00")
        self.assertTrue(push.step())
        self.assertFalse(push.step())
        again = ResultPush("http://nas", TOKEN, pc, SimpleNamespace(results={}), lambda: False, push.state_path, post=post)
        self.assertFalse(again.step())
        self.assertEqual(len(calls), 1)

    def test_waits_while_collection_or_docscan_runs(self):
        calls = []
        busy = [True]
        push, pc = self.pusher("http://nas", busy=lambda: busy[0],
                               post=lambda *a, **k: calls.append(a) or SimpleNamespace(status_code=200, text=""))
        pc.save_run("a", {}, PAYLOAD)
        self.assertFalse(push.step())
        self.assertEqual(calls, [])
        busy[0] = False
        self.assertTrue(push.step())

    def test_failure_is_recorded_without_secrets_and_retried(self):
        attempts = []

        def post(url, **kwargs):
            attempts.append(url)
            if len(attempts) == 1:
                raise requests.ConnectionError(f"{url}?t={TOKEN} 연결 실패")
            return SimpleNamespace(status_code=200, text="")

        push, pc = self.pusher("http://nas", post=post)
        pc.save_run("a", {}, PAYLOAD)
        self.assertFalse(push.step())
        self.assertIn("연결하지 못했습니다", push.state["error"])
        self.assertNotIn(TOKEN, push.state["error"])
        self.assertIsNone(push.state["run"])
        self.assertTrue(push.step())
        self.assertIsNone(push.state["error"])

    def test_wrong_token_is_failure(self):
        _, url = self.server("nas", viewer=True)
        push, pc = self.pusher(url, token="wrong")
        pc.save_run("a", {}, PAYLOAD)
        self.assertFalse(push.step())
        self.assertIn("401", push.state["error"])


class ViewerTest(_Base):
    def post(self, url: str, path: str, body: bytes, token=TOKEN):
        req = urllib.request.Request(url + path, data=body, headers={"X-App-Token": token})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def body(self, **kw):
        return json.dumps({"started_at": "a", "finished_at": "2026-11-05T10:00:00", "params": {}, "payload": PAYLOAD, **kw}).encode()

    def test_viewer_blocks_collection_and_says_so(self):
        _, url = self.server("nas", viewer=True)
        self.assertEqual(self.post(url, "/api/collect", b'{"days": 30}')[0], 409)
        status, data = self.get(url, "/api/notices?days=30&refresh=1")
        self.assertEqual(status, 409)
        self.assertIn("서버 PC", data["error"])
        self.assertFalse(self.get(url, "/api/notices")[1]["canCollect"])

    def test_normal_server_keeps_button_and_refuses_import(self):
        app, url = self.server("pc", viewer=False)
        self.assertTrue(self.get(url, "/api/notices")[1]["canCollect"])
        self.assertEqual(self.post(url, "/api/import-run", self.body())[0], 403)
        self.assertIsNone(app.store.latest_run())

    def test_older_or_same_result_is_skipped(self):
        app, url = self.server("nas", viewer=True)
        self.assertEqual(self.post(url, "/api/import-run", self.body())[1]["skipped"], False)
        self.assertEqual(self.post(url, "/api/import-run", self.body())[1]["skipped"], True)
        old = self.body(finished_at="2026-11-01T10:00:00")
        self.assertEqual(self.post(url, "/api/import-run", old)[1]["skipped"], True)
        self.assertEqual(app.store.latest_run()["id"], 1)

    def test_broken_body_keeps_previous_result(self):
        app, url = self.server("nas", viewer=True)
        self.post(url, "/api/import-run", self.body())
        self.assertEqual(self.post(url, "/api/import-run", self.body()[:40])[0], 400)
        self.assertEqual(self.post(url, "/api/import-run", b'{"payload": {}}')[0], 400)
        self.assertEqual(app.store.latest_run()["id"], 1)

    def test_newer_format_than_nas_code_is_refused(self):
        app, url = self.server("nas", viewer=True)
        newer = {**PAYLOAD, "format": RESULT_FORMAT + 1}
        status, data = self.post(url, "/api/import-run", self.body(payload=newer))
        self.assertEqual(status, 409)
        self.assertIn("예전 버전", data["error"])
        self.assertIsNone(app.store.latest_run())

    def test_token_required(self):
        _, url = self.server("nas", viewer=True)
        self.assertEqual(self.post(url, "/api/import-run", self.body(), token="wrong")[0], 401)


if __name__ == "__main__":
    unittest.main()
