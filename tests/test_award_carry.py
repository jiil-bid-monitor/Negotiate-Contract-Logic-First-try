"""서류 현황을 열 때 착공·착수서류 → 준공·청구(webapp/postaward.py carry_start) — 계약 값을 낙찰 값으로 되돌리지 않고, 날짜를 여러 번 고쳐도 따라오고,
변경계약 금액을 원계약으로 되돌리지 않음(11/25, 예전 코드로는 실패)."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openpyxl import Workbook, load_workbook

from webapp import postaward as pa

KEY = "R26BK00000001-000"


def _book(path: Path, rows: list[tuple]) -> None:
    wb = Workbook(); ws = wb.active; ws.title = "기본사항"
    for r, (label, val) in rows_at(rows):
        ws.cell(r, 1, label); ws.cell(r, 2, val)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def rows_at(rows):
    return list(enumerate(rows, 4))


def _get(path: Path, label: str):
    ws = load_workbook(path)["기본사항"]
    return next(ws.cell(r, 2).value for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == label)


def _put(path: Path, label: str, val) -> None:
    wb = load_workbook(path); ws = wb["기본사항"]
    r = next(r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value == label)
    ws.cell(r, 2).value = val
    wb.save(path)
    t = path.stat().st_mtime + 60 * (1 + _put.n); _put.n += 1
    os.utime(path, (t, t))


_put.n = 0


class CarryStartTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        root = Path(d.name)
        self.tpl, self.folder = root / "tpl", root / "공사" / "R_시험"
        name0, _, _ = pa.DONE_TEMPLATES["공사"]
        _book(self.tpl / name0, [("수신자", None), ("공사명", None), ("계약금액", None), ("준공금액", None), ("계약년월일", None),
                                 ("착공년월일", None), ("준공예정년월일", None), ("귀하", None)])
        _book(self.tpl / pa.CLAIM_TEMPLATE[0], [("수신자", None), ("공사명", None), ("계약금액", None), ("계약년월일", None)])
        _, st, doc = pa.TEMPLATES["공사"]
        self.start = self.folder / st / doc
        _book(self.start, [("공사명", None), ("도급액", None), ("계약일", None), ("착공일", None), ("준공예정일", None), ("수신", None), ("문서번호", None)])
        self.done = self.folder / pa.done_dir(self.folder, "공사") / pa.DONE_TEMPLATES["공사"][2]
        self.app = pa.PostAward(root / "data")
        self.app._write({KEY: {"no": KEY[:-4], "status": "낙찰", "work": "공사", "title": "시험 공사", "institution": "정선군", "qualOnly": False,
                               "folder": str(self.folder), "templates": str(self.tpl), "winner": {"amount": "100000000"},
                               "contract": {"no": "C1", "date": "2026-11-01", "due": "2026-12-31", "amount": "99000000"}}})
        self.nf = SimpleNamespace(title="시험 공사", institution="정선군", raw={})
        self.cv = {"amount": 99000000, "total": 99000000, "contract": datetime(2026, 11, 1), "due": datetime(2026, 12, 31), "to": "정선군 회계과"}
        self.app._fill_done(KEY, self.folder, self.nf, self.tpl, self.cv)

    def test_open_status_keeps_contract_values(self):
        self.app.carry_start(KEY)
        self.assertEqual(_get(self.done, "계약금액"), 99000000)
        self.assertEqual(_get(self.done, "수신자"), "정선군 회계과")

    def test_dates_follow_twice(self):
        for day in (5, 9):
            _put(self.start, "계약일", datetime(2026, 11, day))
            self.assertTrue(self.app.carry_start(KEY))
            self.assertEqual(_get(self.done, "계약년월일"), datetime(2026, 11, day))

    def test_user_edit_in_done_is_kept(self):
        _put(self.done, "계약금액", 1)
        _put(self.start, "도급액", 98000000)
        self.app.carry_start(KEY)
        self.assertEqual(_get(self.done, "계약금액"), 1)

    def test_changed_contract_not_reverted(self):
        _put(self.start, "도급액", 98000000)
        self.app.carry_start(KEY)
        self.assertEqual(_get(self.done, "계약금액"), 98000000)
        items = self.app._read(); items[KEY]["changes"] = [{"at": "2026-11-20", "amount": "120000000"}]
        items[KEY]["contract"]["amount"] = "120000000"; self.app._write(items)
        self.app._fill_done(KEY, self.folder, self.nf, self.tpl, {"amount": 120000000}, force={"amount"})
        self.assertEqual(_get(self.done, "계약금액"), 120000000)
        _put(self.start, "문서번호", "1")  # 금액은 안 고치고 저장
        self.app.carry_start(KEY)
        self.assertEqual(_get(self.done, "계약금액"), 120000000)
        self.app._fill_done(KEY, self.folder, self.nf, self.tpl)  # 서류 다시 만들기(나라장터를 못 본 때)
        self.assertEqual(_get(self.done, "계약금액"), 120000000)
        _put(self.start, "도급액", 121000000)  # 담당자가 다시 고침
        self.app.carry_start(KEY)
        self.assertEqual(_get(self.done, "계약금액"), 121000000)

    def test_locked_file_is_retried(self):
        _put(self.start, "계약일", datetime(2026, 11, 7))
        with mock.patch.object(pa, "fill_done", side_effect=PermissionError):
            self.assertFalse(self.app.carry_start(KEY))
        self.assertTrue(self.app.carry_start(KEY))
        self.assertEqual(_get(self.done, "계약년월일"), datetime(2026, 11, 7))


if __name__ == "__main__":
    unittest.main()
