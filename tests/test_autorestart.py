"""수집 뒤 웹앱 다시 켜기 — 언제 요청하는지, 메모리 기록 읽기."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from webapp.autorestart import IDLE_SECONDS, RestartAfterCollect, memory_report
from webapp.server import busy_now


class RestartTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.request = self.dir / "restart-webapp"
        self.collected = True
        self.is_busy = False
        self.watcher = RestartAfterCollect(self.request, lambda: self.collected, lambda: self.is_busy,
                                           report=lambda: "기록\n")

    def test_no_collection_no_restart(self):
        self.collected = False
        self.assertFalse(self.watcher.check(0))
        self.assertFalse(self.watcher.check(IDLE_SECONDS * 10))
        self.assertFalse(self.request.exists())

    def test_restarts_once_after_idle(self):
        self.assertFalse(self.watcher.check(0))
        self.assertFalse(self.watcher.check(IDLE_SECONDS - 1))
        self.assertFalse(self.request.exists())
        self.assertTrue(self.watcher.check(IDLE_SECONDS))
        self.assertTrue(self.request.exists())
        self.assertEqual((self.dir / "webapp-memory-수집후.txt").read_text(encoding="utf-8"), "기록\n")
        self.request.unlink()
        self.assertFalse(self.watcher.check(IDLE_SECONDS * 2))
        self.assertFalse(self.request.exists())

    def test_busy_resets_the_wait(self):
        self.watcher.check(0)
        self.is_busy = True
        self.assertFalse(self.watcher.check(IDLE_SECONDS))
        self.is_busy = False
        self.assertFalse(self.watcher.check(IDLE_SECONDS + 1))
        self.assertFalse(self.request.exists())
        self.assertTrue(self.watcher.check(IDLE_SECONDS * 2 + 1))

    def test_failed_request_is_retried(self):
        watcher = RestartAfterCollect(self.dir / "없는폴더" / "restart-webapp", lambda: True, lambda: False,
                                      report=lambda: "기록\n")
        watcher.check(0)
        self.assertFalse(watcher.check(IDLE_SECONDS))
        self.assertFalse(watcher.requested)

    def test_memory_file_written_at_most_once_a_minute(self):
        lines = iter(["첫째\n", "둘째\n"])
        watcher = RestartAfterCollect(self.request, lambda: False, lambda: False, report=lambda: next(lines))
        watcher.check(0)
        watcher.check(10)
        self.assertEqual((self.dir / "webapp-memory.txt").read_text(encoding="utf-8"), "첫째\n")
        watcher.check(60)
        self.assertEqual((self.dir / "webapp-memory.txt").read_text(encoding="utf-8"), "둘째\n")


class MemoryReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.status = self.dir / "status"
        self.status.write_text("Name: python\nVmHWM:\t 1048576 kB\nVmRSS:\t  524288 kB\n", encoding="ascii")

    def write(self, name: str, value: str) -> None:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="ascii")

    def test_cgroup_v2(self):
        self.write("memory.max", str(2 * 1024 ** 3))
        self.write("memory.current", str(1024 ** 3))
        self.write("memory.peak", str(1024 ** 3 * 3 // 2))
        report = memory_report(self.dir, self.status)
        self.assertIn("제한: 2.00GB", report)
        self.assertIn("현재 1.00GB · 최고 1.50GB", report)
        self.assertIn("웹앱 자체: 현재 0.50GB · 최고 1.00GB", report)

    def test_cgroup_v1_without_limit(self):
        self.write("memory/memory.limit_in_bytes", str(2 ** 63 - 4096))
        self.write("memory/memory.usage_in_bytes", str(1024 ** 3))
        self.assertIn("제한: 제한 없음", memory_report(self.dir, self.status))

    def test_cgroup_v2_max(self):
        self.write("memory.max", "max")
        self.assertIn("제한: 제한 없음", memory_report(self.dir, self.status))

    def test_nothing_to_read(self):
        report = memory_report(self.dir / "없음", self.dir / "없음.txt")
        self.assertIn("제한: 확인 안 됨", report)
        self.assertIn("웹앱 자체: 현재 확인 안 됨", report)


class BusyTest(unittest.TestCase):
    def app(self, job=False, scan=False):
        return SimpleNamespace(collector=SimpleNamespace(job=SimpleNamespace(running=job)),
                               docscan=SimpleNamespace(state={"running": scan}))

    def test_job_and_scan(self):
        self.assertFalse(busy_now(self.app()))
        self.assertTrue(busy_now(self.app(job=True)))
        self.assertTrue(busy_now(self.app(scan=True)))


if __name__ == "__main__":
    unittest.main()
