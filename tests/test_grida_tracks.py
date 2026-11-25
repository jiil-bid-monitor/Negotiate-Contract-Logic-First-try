"""그리다 실적 모으기(scripts/grida_tracks.py) — 사업 폴더 문서 → 실적 엑셀 → 정량평가서 실적목록(11/25 사용자 요청)."""
import importlib.util
from datetime import date
from pathlib import Path

from quantdocs import importer, vault

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("grida_tracks", ROOT / "scripts" / "grida_tracks.py")
gt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gt)


def test_scan_project_folder_to_orders(tmp_path, monkeypatch):
    root = tmp_path / "04_그리다" / "[진행완료]"
    p = root / "2025_가상공원 놀이시설(제작설치)_241213"
    (p / "계약").mkdir(parents=True)
    (p / "계약" / "계약서.pdf").write_bytes(b"x")
    (root / "2024_사무실_이전").mkdir()
    texts = {"계약서.pdf": "물품구매 계약서\n계 약 금 액 : 금 일십이억 원정 (￦1,226,268,500)\n"
                         "계약기간 : 2024. 12. 05. ~ 2025. 05. 26.\n수요기관 : 가상시 체험관\n"}
    monkeypatch.setattr(gt, "_text", lambda f: texts.get(f.name, ""))
    folders = gt.projects(tmp_path / "04_그리다")
    assert [f.name for f in folders] == [p.name]  # 사무실 이전은 사업 아님
    row = gt.read_project(folders[0])
    assert row["사업명"] == "가상공원 놀이시설(제작설치)"
    assert row["금액"] == 1226268500 and row["시작"] == date(2024, 12, 5) and row["끝"] == date(2025, 5, 26)
    assert row["발주기관"] == "가상시 체험관" and not row["확인"]
    out = gt.write([row], tmp_path / "공사수주현황_그리다.xlsx")
    orders = importer.read_orders(out)
    assert orders[0]["계약금액(원)"] == 1226268500 and orders[0]["발주기관"] == "가상시 체험관"
    assert orders[0]["준공일"] == date(2025, 5, 26)
    merged = vault.merge_tracks([], orders)
    assert merged[0]["발주기관"] == "가상시 체험관"
