"""서류 목록(config/documents.json) 읽기와 폴더 점검."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "config" / "documents.json"
DOC_SUFFIXES = {".pdf", ".hwp", ".hwpx", ".xlsx", ".xls", ".jpg", ".jpeg", ".png", ".docx"}


@dataclass
class Document:
    id: str
    name: str
    form: str
    per_notice: str
    automation: str
    human: str = ""
    note: str = ""
    filename_keywords: list[str] = field(default_factory=list)


def load_catalog(path: Path = DEFAULT_CATALOG) -> list[Document]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Document(**{k: v for k, v in d.items() if k in Document.__dataclass_fields__}) for d in data["documents"]]


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def match_files(doc: Document, files: list[Path]) -> list[Path]:
    """파일 이름(띄어쓰기 무시)에 서류 낱말이 하나라도 든 파일."""
    words = [_squash(w) for w in doc.filename_keywords if w.strip()]
    return [f for f in files if any(w in _squash(f.stem) for w in words)]


def scan(folder: Path, catalog: list[Document]) -> dict[str, list[Path]]:
    """폴더(하위 포함)의 문서 파일을 서류별로 나눔. 낱말이 없는 서류(재발급 등)는 빈 목록."""
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in DOC_SUFFIXES)
    return {d.id: match_files(d, files) for d in catalog}
