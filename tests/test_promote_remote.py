"""'실제 공고로 올리기'를 NAS 한 곳에(11/25) — 서버 PC에서 올리면 NAS에 남고, 이 PC에 남아 있던 것은 한 번 옮기고, NAS가 안 되면 지난번 것."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_webapp import FakePast  # noqa: E402
from webapp.collect import Collector  # noqa: E402
from webapp.server import App, make_handler  # noqa: E402
from webapp.store import Store  # noqa: E402


class PromoteRemoteTest(unittest.TestCase):
    def _app(self):
        folder = Path(tempfile.mkdtemp())
        store = Store(folder / "webapp.sqlite3")
        past = FakePast()
        app = App(store, Collector(store, past), past, token="tok")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return app, srv, f"http://127.0.0.1:{srv.server_address[1]}", folder

    def _call(self, base, path, data=None):
        req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"X-App-Token": "tok", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def test_promote_goes_to_nas(self):
        nas, nas_srv, nas_url, _ = self._app()
        pc, _, pc_url, pc_dir = self._app()
        pc.team.promote({"noticeNo": "R1", "by": "예전", "title": "이 PC에 남은 것", "on": True})
        pc.team_remote = (nas_url, "tok")
        co = pc.companies["jiil"]
        self.assertIn("R1", pc.promoted(co))  # 처음 볼 때 NAS로 옮김
        self.assertEqual(nas.team.promoted["R1"]["by"], "예전")
        self.assertEqual(pc.team.promoted, {})
        self.assertTrue((pc_dir / "promoted_옮기기전.json").is_file())
        self._call(pc_url, "/api/promote", {"noticeNo": "R2", "by": "시험", "title": "새로 올림", "on": True})
        self.assertIn("R2", nas.team.promoted)
        self.assertIn("R2", pc.promoted(co))  # 바로 보임
        self._call(pc_url, "/api/promote", {"noticeNo": "R2", "by": "시험", "on": False})
        self.assertNotIn("R2", nas.team.promoted)
        nas.team.promote({"noticeNo": "R3", "by": "NAS", "title": "NAS에서 올림", "on": True})
        pc.promo_cache.clear()
        self.assertIn("R3", pc.promoted(co))
        # NAS가 꺼짐 — 목록은 지난번 것, 올리기는 오류(이 PC에 몰래 남기지 않음)
        nas_srv.shutdown(); nas_srv.server_close()
        pc.promo_cache["jiil"] = (0.0, pc.promo_cache["jiil"][1])
        self.assertIn("R3", pc.promoted(co))
        with self.assertRaises(urllib.error.HTTPError):
            self._call(pc_url, "/api/promote", {"noticeNo": "R4", "by": "시험", "title": "x", "on": True})
        self.assertEqual(pc.team.promoted, {})

    def test_without_remote_unchanged(self):
        app, _, url, _ = self._app()
        self._call(url, "/api/promote", {"noticeNo": "R1", "by": "시험", "title": "t", "on": True})
        self.assertIn("R1", app.team.promoted)


if __name__ == "__main__":
    unittest.main()
