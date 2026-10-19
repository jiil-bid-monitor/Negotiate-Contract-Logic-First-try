"""싱크로율 자료 묶음 암호화·복호화 (2026-10-19 통합 결정 16번)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nego import corpus  # noqa: E402


class TestCorpus(unittest.TestCase):
    def test_round_trip_and_wrong_password(self):
        sealed = corpus.seal(b"hello profiles", "pw")
        self.assertTrue(sealed.startswith(b"JCORP1"))
        self.assertEqual(corpus.unseal(sealed, "pw"), b"hello profiles")
        with self.assertRaises(Exception):
            corpus.unseal(sealed, "wrong")

    def test_seal_and_open_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "jiil"
            (repo / "docs/summaries").mkdir(parents=True)
            (repo / "docs/summaries/past_task_profiles.json").write_text("[]", encoding="utf-8")
            (repo / "evaluation").mkdir()
            (repo / "evaluation/task_profile.py").write_text("X = 1\n", encoding="utf-8")
            sealed_path, opened = Path(tmp) / "b.enc", Path(tmp) / "out"
            with mock.patch.object(corpus, "SEALED", sealed_path), mock.patch.object(corpus, "OPENED", opened), \
                    mock.patch.dict("os.environ", {"CORPUS_PASSPHRASE": "pw"}):
                self.assertEqual(corpus.main(["seal", "--jiil-repo", str(repo)]), 0)
                self.assertEqual(corpus.main(["open"]), 0)
            self.assertEqual((opened / "evaluation/task_profile.py").read_text(encoding="utf-8"), "X = 1\n")

    def test_no_passphrase_is_noop(self):
        with mock.patch.dict("os.environ", {"CORPUS_PASSPHRASE": ""}):
            self.assertEqual(corpus.main(["open"]), 0)


if __name__ == "__main__":
    unittest.main()
