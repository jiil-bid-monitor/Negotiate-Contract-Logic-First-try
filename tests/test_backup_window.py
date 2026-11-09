"""NAS 백업 시간에는 수집을 쉰다 — 토요일 03시~일요일 03시."""

import unittest
from datetime import datetime

from webapp.server import in_backup_window


class BackupWindowTest(unittest.TestCase):
    def test_edges(self):
        self.assertFalse(in_backup_window(datetime(2026, 11, 14, 2, 59)))  # 토 02:59
        self.assertTrue(in_backup_window(datetime(2026, 11, 14, 3, 0)))
        self.assertTrue(in_backup_window(datetime(2026, 11, 15, 2, 59)))  # 일 02:59
        self.assertFalse(in_backup_window(datetime(2026, 11, 15, 3, 0)))
        self.assertFalse(in_backup_window(datetime(2026, 11, 13, 12, 0)))  # 금


if __name__ == "__main__":
    unittest.main()
