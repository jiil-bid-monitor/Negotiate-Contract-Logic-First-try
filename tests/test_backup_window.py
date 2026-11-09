"""NAS 백업 시간·바쁠 때는 수집을 쉰다 — 토요일 03시~일요일 03시, CPU 60%·RAM 75% 넘음."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from webapp import nasload
from webapp.server import in_backup_window


class BackupWindowTest(unittest.TestCase):
    def test_edges(self):
        self.assertFalse(in_backup_window(datetime(2026, 11, 14, 2, 59)))  # 토 02:59
        self.assertTrue(in_backup_window(datetime(2026, 11, 14, 3, 0)))
        self.assertTrue(in_backup_window(datetime(2026, 11, 15, 2, 59)))  # 일 02:59
        self.assertFalse(in_backup_window(datetime(2026, 11, 15, 3, 0)))
        self.assertFalse(in_backup_window(datetime(2026, 11, 13, 12, 0)))  # 금


class NasLoadTest(unittest.TestCase):
    def test_ram_uses_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            meminfo = Path(tmp) / "meminfo"
            meminfo.write_text("MemTotal: 4000000 kB\nMemFree: 100000 kB\nMemAvailable: 1000000 kB\n")
            self.assertAlmostEqual(nasload.ram_percent(meminfo), 75.0)

    def test_cpu_counts_io_wait(self):
        samples = iter(["cpu  10 0 10 100 0 0 0 0\n", "cpu  20 0 20 120 60 0 0 0\n"])
        with patch.object(Path, "read_text", lambda self, *a, **k: next(samples)):
            self.assertAlmostEqual(nasload.cpu_percent(Path("가짜"), seconds=0), 80.0)

    def test_busy_reason_limits(self):
        with patch.object(nasload, "cpu_percent", return_value=61.0), patch.object(nasload, "ram_percent", return_value=20.0):
            self.assertIn("CPU 61%", nasload.busy_reason(seconds=0))
        with patch.object(nasload, "cpu_percent", return_value=30.0), patch.object(nasload, "ram_percent", return_value=76.0):
            self.assertIn("RAM 76%", nasload.busy_reason(seconds=0))
        with patch.object(nasload, "cpu_percent", return_value=60.0), patch.object(nasload, "ram_percent", return_value=75.0):
            self.assertIsNone(nasload.busy_reason(seconds=0))
        with patch.object(nasload, "cpu_percent", side_effect=OSError):
            self.assertIsNone(nasload.busy_reason(seconds=0))


if __name__ == "__main__":
    unittest.main()
