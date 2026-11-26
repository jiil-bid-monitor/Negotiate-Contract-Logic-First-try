"""시험이 저장소 data/ 폴더의 회사 서류 기록(신청 상태·'추가 필요')을 건드리지 않게 임시 폴더로."""
import pytest


@pytest.fixture(autouse=True)
def _doc_state_files(tmp_path, monkeypatch):
    from webapp import docexpiry

    monkeypatch.setattr(docexpiry, "APPLY_PATH", tmp_path / "doc_applying.json")
