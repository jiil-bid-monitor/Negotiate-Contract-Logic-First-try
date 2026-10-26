"""투찰 이력 암호화 묶음 (scripts/bid_history_vault.py, 2026-10-26)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bid_history_vault as vault  # noqa: E402

PW = "correct horse battery staple"


class TestBidHistoryVault(unittest.TestCase):
    def test_seal_open_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out, sealed = Path(tmp) / "src", Path(tmp) / "out", Path(tmp) / "b.enc"
            (src / "2014").mkdir(parents=True)
            (src / "2014" / "이력.xls").write_bytes(b"<table>1</table>")
            (src / "목록.csv").write_text("R26BK01758791-000,용역\n", encoding="utf-8")
            with mock.patch.object(vault, "SEALED", sealed), mock.patch.dict("os.environ", {vault.ENV: PW}):
                self.assertEqual(vault.main(["seal", "--dir", str(src)]), 0)
                self.assertNotIn(b"R26BK01758791", sealed.read_bytes())
                self.assertEqual(vault.main(["open", "--dir", str(out)]), 0)
            self.assertEqual((out / "2014" / "이력.xls").read_bytes(), b"<table>1</table>")
            self.assertEqual((out / "목록.csv").read_text(encoding="utf-8"), "R26BK01758791-000,용역\n")

    def test_wrong_password_and_short_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, sealed = Path(tmp) / "src", Path(tmp) / "b.enc"
            src.mkdir()
            (src / "a.xls").write_bytes(b"x")
            with mock.patch.object(vault, "SEALED", sealed):
                with mock.patch.dict("os.environ", {vault.ENV: "short"}):
                    self.assertEqual(vault.main(["seal", "--dir", str(src)]), 1)
                    self.assertFalse(sealed.exists())
                with mock.patch.dict("os.environ", {vault.ENV: PW}):
                    self.assertEqual(vault.main(["seal", "--dir", str(src)]), 0)
                with mock.patch.dict("os.environ", {vault.ENV: PW + "!"}):
                    self.assertEqual(vault.main(["open", "--dir", str(Path(tmp) / "out")]), 1)

    def test_no_passphrase(self):
        with mock.patch.dict("os.environ", {vault.ENV: ""}):
            self.assertEqual(vault.main(["list"]), 1)


if __name__ == "__main__":
    unittest.main()
