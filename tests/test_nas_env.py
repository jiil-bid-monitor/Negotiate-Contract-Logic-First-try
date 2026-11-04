"""NAS 실행 스크립트의 .env 읽기 — 특수문자를 글자 그대로, compose 값 우선."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nas" / "run_with_env.py"
spec = importlib.util.spec_from_file_location("run_with_env", SCRIPT)
run_with_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_with_env)


class ReadEnvTest(unittest.TestCase):
    def read(self, text: bytes) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_bytes(text)
            return run_with_env.read_env(path)

    def test_values_kept_literally(self):
        env = self.read("A=a&b;c!$d\nB='x y'\nC=\"q\"\n#D=off\n\nexport E=1\nF=k=v==\n".encode())
        self.assertEqual(env, {"A": "a&b;c!$d", "B": "x y", "C": "q", "E": "1", "F": "k=v=="})

    def test_bom_crlf_and_inner_quotes(self):
        env = self.read(b"\xef\xbb\xbfHWP='java -jar \"/a b/c.jar\" \"{src}\"'\r\nK=1\r\n")
        self.assertEqual(env, {"HWP": 'java -jar "/a b/c.jar" "{src}"', "K": "1"})


class MergeTest(unittest.TestCase):
    def test_compose_values_win_only_when_set(self):
        env = run_with_env.merged_env({"BACKUP_DIR": "C:/x", "PORT": "1", "HOST": "h", "KEY": "new"},
                                      {"BACKUP_DIR": "", "PORT": "8765", "KEY": "old", "PATH": "/bin"})
        self.assertEqual(env, {"BACKUP_DIR": "", "PORT": "8765", "HOST": "h", "KEY": "new", "PATH": "/bin"})


if __name__ == "__main__":
    unittest.main()
