"""회사 서류 폴더의 경쟁입찰참가자격등록증 PDF로 보유 자격 더하기 (2026-11-24 사용자 요청 — 그리다).

사용자: "추가해야 할 면허가 있다면 폴더에 추가했을 때 읽을 수 있으면 돼" — 서버 PC 서류 폴더(그리다 = grida-quant-docs,
`quantdocs.vault.company_dir`)에 나라장터에서 출력한 '경쟁입찰참가자격등록증' PDF를 넣으면 수집할 때 그 등록 업종·물품을
보유 자격 파일(config/companies/<id>/held_qualifications.json)에 **더해서** 씀. 파일에 이미 있는 것은 그대로(빼지 않음) —
등록유효기간이 지난 물품은 더하지 않음. 지일은 잠금 묶음·손으로 다듬은 파일이라 이 규칙을 안 씀.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_NAME = re.compile(r"입찰참가자격등록증|참가자격등록증|입찰참가등록증")
_SKIP_DIR = re.compile(r"^(예시|샘플|참고|\[이전 서류\]|이전)")
_PRODUCT_ROW = re.compile(r"(\d{4}/\d{2}/\d{2})\s+(\d{10})\s+")
_DATE = re.compile(r"(\d{4})/(\d{2})/(\d{2})")
_CACHE: dict[tuple[str, float], dict[str, Any]] = {}


def parse(text: str) -> dict[str, Any]:
    """등록증 글 → {"industries": [{code, name}], "products": [{code, name, until}]}."""
    products: list[dict[str, Any]] = []
    start = text.find("입찰참가 등록물품")
    end = min([i for i in (text.find("* 등록유효기간"), text.find("등록업종 및 시공능력")) if i > start] or [len(text)])
    if start >= 0:
        part = text[start:end]
        rows = list(_PRODUCT_ROW.finditer(part))
        for i, m in enumerate(rows):
            chunk = part[m.end(): rows[i + 1].start() if i + 1 < len(rows) else len(part)]
            name_m = re.match(r"(.+?)\s+[YN]\s", chunk, re.S)
            if not name_m:
                continue
            name = re.sub(r"\s+", "", name_m.group(1))
            # 날짜가 '2027/10/\n12'처럼 줄에서 잘림 — 줄바꿈을 지우고 찾음. 마지막 날짜 = 등록유효기간 끝
            dates = _DATE.findall(re.sub(r"/\s*\n\s*", "/", chunk[name_m.end():]))
            until = "-".join(dates[-1]) if dates else ""
            products.append({"code": m.group(2), "name": name, "until": until})
    industries: list[dict[str, str]] = []
    start = text.find("등록업종 및 시공능력")
    if start >= 0:
        stop = text.find("주력분야", start)
        flat = re.sub(r"\s*\n\s*", "", text[start: stop if stop > 0 else len(text)])
        for m in re.finditer(r"(?<!\d)(\d{4})\(", flat):
            depth, j = 1, m.end()
            while j < len(flat) and depth:
                depth += {"(": 1, ")": -1}.get(flat[j], 0)
                j += 1
            name = flat[m.end(): j - 1].strip()
            if name and all(e["code"] != m.group(1) for e in industries):
                industries.append({"code": m.group(1), "name": name})
    return {"industries": industries, "products": products}


def find(folder: Path) -> Path | None:
    """폴더(하위 폴더 4단계까지) 안 등록증 PDF 중 가장 최근 것."""
    if not folder.is_dir():
        return None
    found: list[Path] = []
    for p in folder.rglob("*.pdf"):
        rel = p.relative_to(folder).parts
        if len(rel) > 5 or any(_SKIP_DIR.match(x) for x in rel[:-1]):
            continue
        if _NAME.search(re.sub(r"\s+", "", p.stem)):
            found.append(p)
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def read(path: Path) -> dict[str, Any] | None:
    key = (str(path), path.stat().st_mtime)
    if key not in _CACHE:
        try:
            import pypdf

            text = "\n".join(page.extract_text() or "" for page in pypdf.PdfReader(str(path)).pages)
        except Exception as exc:  # noqa: BLE001 — 못 읽으면 보유 자격 파일만 씀
            log.warning("등록증 PDF를 읽지 못함 (%s): %s", path.name, exc)
            return None
        _CACHE.clear()
        _CACHE[key] = parse(text)
    return _CACHE[key]


def merge(held: dict[str, Any], cert: dict[str, Any], today: date | None = None, source: str = "") -> dict[str, Any]:
    """보유 자격에 등록증의 업종·물품을 더한 사본 — 이미 있는 코드는 그대로, 등록유효기간이 지난 물품은 안 더함."""
    today_s = (today or date.today()).isoformat()
    out = dict(held)
    note = f"등록증 PDF에서 읽음({source})" if source else "등록증 PDF에서 읽음"
    have = {str(e.get("code")) for e in held.get("heldIndustries", [])}
    out["heldIndustries"] = list(held.get("heldIndustries", [])) + [
        {"code": e["code"], "name": e["name"], "_source": note} for e in cert.get("industries", []) if e["code"] not in have]
    have = {str(e.get("code")) for e in held.get("heldProducts", [])}
    out["heldProducts"] = list(held.get("heldProducts", [])) + [
        {"code": e["code"], "name": e["name"], "_until": e["until"], "_source": note}
        for e in cert.get("products", []) if e["code"] not in have and (not e["until"] or e["until"] >= today_s)]
    return out


def apply(held: dict[str, Any], company: str | None) -> dict[str, Any]:
    """지일이 아닌 회사면 그 회사 서류 폴더의 등록증으로 더한 사본, 아니면(또는 등록증이 없으면) 그대로."""
    if not company or company == "jiil":
        return held
    try:
        from quantdocs.vault import company_dir

        path = find(company_dir(company))
    except Exception:  # noqa: BLE001
        return held
    cert = read(path) if path else None
    if not cert:
        return held
    merged = merge(held, cert, source=path.name)
    added = [e for k in ("heldIndustries", "heldProducts") for e in merged[k] if e.get("_source")]
    if added:
        log.info("등록증 PDF(%s)에서 보유 자격 %d개를 더함: %s", path.name, len(added),
                 ", ".join(f"{e['name']}({e['code']})" for e in added))
    return merged
