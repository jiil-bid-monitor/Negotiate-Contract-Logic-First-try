"""통합본 — 공고가 요구하는 회사 서류를 **파일 이름 키워드로** 골라 원본 그대로 이어 붙인 PDF 한 개.

(11/03 사용자 결정: 회사 서류 내용을 읽어(스캔·글 추출) 고르지 않고, 파일 제목 키워드로 골라 그대로 가져와 통합본을 만든다.)

    picks = pick(vault.files, 제출서류 글 목록, 요구 자격)   # 무엇을 어떤 순서로 넣을지
    merge(picks, vault, out_pdf)                             # 목차 한 장 + 원본 PDF·그림을 순서대로

순서: 공고 '제출서류' 항목 순서(config/bundle_keywords.json의 item → files 낱말) → 그 밖의 요구 자격 증빙.
요구 자격(업종·세부품명)은 자격 이름과 파일 이름을 맞춘다 — 예: 산업디자인전문회사(환경디자인분야) ↔ 산업디자인전문회사신고확인증.pdf,
실물모형및전시물 ↔ 실물모형(~27.7.26).pdf. 파일 이름의 '(~27.7.26)' 같은 유효기간은 파일 이름에서만 읽어 지났으면 표시한다.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "bundle_keywords.json"
MERGEABLE = (".pdf", ".jpg", ".jpeg", ".png")


@dataclass
class Pick:
    section: str  # 통합본 목차 줄 이름(제출서류 항목 또는 자격 이름)
    path: str  # 회사 자료 묶음 안 경로(files/…)
    note: str = ""  # 유효기간 지남 등


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _stem(path: str) -> str:
    """파일 이름에서 확장자·괄호(유효기간 등)를 뺀 핵심 — '실물모형(~27.7.26).pdf' → '실물모형'."""
    base = Path(path).stem
    return _sq(re.sub(r"\([^)]*\)|\[[^\]]*\]", "", base))


def _core(name: str) -> str:
    """자격 이름의 핵심 — '산업디자인전문회사(환경디자인분야)' → '산업디자인전문회사'."""
    return _sq(re.sub(r"\(.*$", "", str(name or "")))


def _code_names() -> dict[str, str]:
    """코드만 적힌 요구 자격에 이름을 붙이려고 — 프로젝트 1 이름 사전(있을 때만)."""
    from .notice import _NEGO_CANDIDATES
    import os

    out: dict[str, str] = {}
    for p in [str(ROOT), os.environ.get("NEGO_REPO", "")] + [str(ROOT.parent / n) for n in _NEGO_CANDIDATES]:
        cfg = Path(p) / "config" if p else None
        if not cfg or not cfg.is_dir():
            continue
        try:
            out.update(json.loads((cfg / "code_names.json").read_text(encoding="utf-8")).get("names", {}))
            held = json.loads((cfg / "held_qualifications.json").read_text(encoding="utf-8"))
            for k in ("heldIndustries", "heldProducts"):
                out.update({e["code"]: e["name"] for e in held.get(k, []) if e.get("code")})
        except (OSError, ValueError):
            pass
        break
    return out


def _expiry_note(path: str, today: date) -> str:
    from .importer import validity_from_name

    end, _ = validity_from_name(Path(path).name)
    return f"파일 이름상 유효기간 {end:%Y-%m-%d} 지남 — 최신본 확인" if end and end < today else ""


def pick(files, submit_texts: list[str], quals: list[dict], today: date | None = None) -> tuple[list[Pick], list[str]]:
    """(넣을 파일 순서, 못 찾은 것). files: 묶음 안 경로들(files/… 만 씀)."""
    today = today or date.today()
    rules = json.loads(CONFIG.read_text(encoding="utf-8"))["rules"]
    pool = [f for f in files if f.startswith("files/") and Path(f).suffix.lower() in MERGEABLE + (".hwp", ".hwpx")]
    picks: list[Pick] = []
    missing: list[str] = []
    used: set[str] = set()

    def add(section: str, path: str) -> None:
        if path not in used:
            used.add(path)
            picks.append(Pick(section, path, _expiry_note(path, today)))

    # 요구 자격 → 맞는 파일(이름 대조)
    names = _code_names()
    qual_hits: list[tuple[str, list[str], bool]] = []  # (자격 이름, 파일들, 직접생산 품목인가)
    for q in quals:
        found: list[tuple[str, list[str], bool]] = []
        for it in q.get("items", []):
            if not it.get("held"):
                continue
            label = it.get("name") or ""
            if not label or label == it.get("code") or label.startswith("이름 미확인"):
                label = names.get(it.get("code") or "", label)
            cands = {c for c in (_core(label), _core(names.get(it.get("code") or "", ""))) if len(c) >= 2}
            # 서류상태에 그 코드로 적힌 파일(정확) → 없으면 파일 이름 대조. 이름만 적힌 증빙(중소기업확인서 등)은 keys 낱말로
            hits = [f for f in it.get("paths") or [] if f in pool]
            if not hits and it.get("keys"):
                hits = [f for f in pool if any(k in _sq(Path(f).name) for k in it["keys"])]
            if not hits and not it.get("keys"):
                hits = [f for f in pool if any(c in _sq(Path(f).name) or (len(_stem(f)) >= 2 and _stem(f) in c) for c in cands)]
            title = f"{label}({it['code']})" if it.get("code") and it["code"] not in label else label
            found.append((title, hits, len(str(it.get("code") or "")) >= 8 or label == "직접생산확인증명서"))
        if len(q.get("items", [])) > 1:
            # '이 중 하나' — 서류가 있는 첫 자격 하나만 통합본에(11/04 사용자 요청)
            one = next((h for h in found if h[1]), None)
            if one:
                qual_hits.append(one)
            elif found:
                missing.append(f"자격 {' 또는 '.join(h[0] for h in found)}: 파일 이름이 맞는 서류 없음")
            continue
        for h in found:
            qual_hits.append(h)
            if not h[1]:
                missing.append(f"자격 {h[0]}: 파일 이름이 맞는 서류 없음")

    # 1) 제출서류 항목 순서대로
    for text in submit_texts:
        t = _sq(text)
        for r in rules:
            if not any(_sq(w) in t for w in r["item"]):
                continue
            if r.get("by_qualification"):
                for title, hits, product in qual_hits:
                    if r["by_qualification"] == "all" or product:
                        for f in hits:
                            add(f"{r['label']} — {title}", f)
                continue
            hits = [f for f in pool if any(_sq(w) in _sq(Path(f).name) for w in r["files"])]
            if not hits:
                missing.append(f"제출서류 '{text[:40]}': 파일 이름에 '{'·'.join(r['files'])}' 든 서류 없음")
            for f in hits:
                add(r["label"], f)
    # 2) 그 밖의 요구 자격 증빙
    for title, hits, _ in qual_hits:
        for f in hits:
            add(f"자격 — {title}", f)
    return picks, list(dict.fromkeys(missing))


# ── 이어 붙이기 ───────────────────────────────────────────────
_FONTS = ["C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/gulim.ttc",
          "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
          "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"]


def _toc_page(title: str, rows: list[tuple[str, str, int]]) -> bytes | None:
    """목차 한 장(한글 글꼴이 있을 때만). rows: (구분, 파일 이름, 시작 쪽)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except ImportError:
        return None
    font = next((f for f in _FONTS if Path(f).exists()), None)
    if not font:
        return None
    try:
        pdfmetrics.registerFont(TTFont("KR", font, subfontIndex=0) if font.endswith(".ttc") else TTFont("KR", font))
    except Exception:  # noqa: BLE001 — 글꼴 문제면 목차 없이
        return None
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    def wrap(text: str, size: float, width: float) -> list[str]:
        """쪽 너비 안에서 줄바꿈(글자 단위) — 긴 공고명이 오른쪽으로 잘리던 것(11/04)."""
        out, cur = [], ""
        for ch in text:
            if pdfmetrics.stringWidth(cur + ch, "KR", size) > width and cur:
                out.append(cur)
                cur = ch.lstrip()
            else:
                cur += ch
        return out + ([cur] if cur else [])

    y = h - 60
    c.setFont("KR", 15)
    for line in wrap(title, 15, w - 100):
        c.drawString(50, y, line)
        y -= 20
    y -= 10
    c.setFont("KR", 10)
    for i, (sec, name, page) in enumerate(rows, 1):
        lines = wrap(f"{i}. {sec}", 10, w - 140)
        names = wrap(name, 10, w - 116)
        if y - 13 * len(lines) - 13 * len(names) < 40:
            c.showPage()
            c.setFont("KR", 10)
            y = h - 50
        c.drawRightString(w - 50, y, f"{page}쪽")
        for line in lines:
            c.drawString(50, y, line)
            y -= 13
        c.setFillGray(0.45)
        for line in names:
            c.drawString(66, y, line)
            y -= 13
        c.setFillGray(0)
        y -= 5
    c.save()
    return buf.getvalue()


def _as_pdf(name: str, data: bytes) -> bytes | None:
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return data
    if ext in (".jpg", ".jpeg", ".png"):
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(data)).convert("RGB")
            out = io.BytesIO()
            img.save(out, "PDF", resolution=150)
            return out.getvalue()
        except Exception:  # noqa: BLE001
            return None
    return None


def merge(picks: list[Pick], vault, out_pdf: Path, title: str = "정량평가서 제출 서류") -> tuple[Path | None, list[str]]:
    """picks 순서대로 이어 붙인 PDF. (결과, 못 넣은 것). 목차는 한글 글꼴이 있으면 맨 앞에 한 장, 책갈피는 늘."""
    from pypdf import PdfReader, PdfWriter

    skipped: list[str] = []
    parts: list[tuple[Pick, PdfReader]] = []
    for p in picks:
        pdf = _as_pdf(p.path, vault.files.get(p.path, b""))
        if pdf is None:
            skipped.append(f"{Path(p.path).name}: PDF·그림이 아니라 통합본에 못 넣음(첨부 폴더에만)")
            continue
        try:
            parts.append((p, PdfReader(io.BytesIO(pdf))))
        except Exception as err:  # noqa: BLE001 — 깨진 PDF
            skipped.append(f"{Path(p.path).name}: PDF를 열지 못함({type(err).__name__})")
    if not parts:
        return None, skipped
    # 목차 쪽수를 알려면 목차 장 수가 필요 — 한 번 만들어 보고 쪽수를 정함
    def rows(offset: int):
        page, out = offset + 1, []
        for p, r in parts:
            out.append((p.section, Path(p.path).name, page))
            page += len(r.pages)
        return out

    toc = _toc_page(title, rows(1))
    if toc:
        n = len(PdfReader(io.BytesIO(toc)).pages)
        toc = _toc_page(title, rows(n)) if n != 1 else toc
    w = PdfWriter()
    if toc:
        for pg in PdfReader(io.BytesIO(toc)).pages:
            w.add_page(pg)
    else:
        skipped.append("한글 글꼴을 찾지 못해 목차 장 없이 만듦(책갈피는 있음)")
    for p, r in parts:
        start = len(w.pages)
        for pg in r.pages:
            w.add_page(pg)
        w.add_outline_item(f"{p.section} — {Path(p.path).name}", start)
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with out_pdf.open("wb") as fh:
        w.write(fh)
    return out_pdf, skipped
