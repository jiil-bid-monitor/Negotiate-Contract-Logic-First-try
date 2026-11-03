"""공고번호 → 나라장터 공고 정보·첨부 파일 받기, 그리고 요구 자격을 회사 자료(보유 코드)와 맞춰 보기.

프로젝트 1(`nego`)의 조회·첨부 읽기·자격 판정 코드를 가져다 쓴다. 나라장터 인증키는 프로젝트 1 폴더의 `.env`
(서버 PC 웹앱이 쓰는 것)나 환경변수 `NARA_SERVICE_KEY`에서 읽는다.
"""

from __future__ import annotations

import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from .notice import _import_nego, _NEGO_CANDIDATES, ROOT

log = logging.getLogger(__name__)
DOC_EXTS = {"hwp", "hwpx", "pdf", "docx", "xlsx", "xls"}


def nego_repo() -> Path | None:
    for p in [str(ROOT), os.environ.get("NEGO_REPO", "")] + [str(ROOT.parent / n) for n in _NEGO_CANDIDATES]:
        if p and (Path(p) / "nego" / "attachments.py").exists():
            return Path(p)
    return None


def service_key() -> str:
    """환경변수 → 프로젝트 1의 .env 순. .env의 키는 인코딩된 값이라 한 번 푼다(프로젝트 1 10/21 메모)."""
    key = os.environ.get("NARA_SERVICE_KEY", "").strip()
    if not key and (repo := nego_repo()) and (repo / ".env").exists():
        for line in (repo / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("NARA_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return unquote(key) if "%" in key else key


@dataclass
class NoticeFiles:
    no: str
    ord: str = ""
    title: str = ""
    institution: str = ""
    work_type: str = ""
    award_method: str = ""
    contract_method: str = ""
    posted: str = ""
    licenses: list[str] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # 못 받은 첨부(이유)

    @property
    def negotiation(self) -> bool:
        return "협상" in self.award_method


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return name[:120] or "첨부"


def _save(data: bytes, name: str, out_dir: Path, attachments) -> list[Path]:
    """첨부 하나 저장. zip이면 안의 문서만 풀어 저장(한 겹)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    sniff = attachments._sniff_ext(data)
    if sniff and ext not in DOC_EXTS | {"zip"}:
        ext = sniff
        name = f"{name}.{ext}"
    if ext == "zip" or (sniff == "zip" and ext not in DOC_EXTS):
        saved = []
        with zipfile.ZipFile(BytesIO(data)) as z:
            for info in z.infolist():
                inner = attachments._zip_member_name(info)
                iext = inner.rsplit(".", 1)[-1].lower() if "." in inner else ""
                if info.is_dir() or iext not in DOC_EXTS or info.file_size > 80 * 1024 * 1024:
                    continue
                p = out_dir / _safe(inner.rsplit("/", 1)[-1])
                p.write_bytes(z.read(info))
                saved.append(p)
        return saved
    if ext not in DOC_EXTS:
        return []
    p = out_dir / _safe(name)
    p.write_bytes(data)
    return [p]


def fetch_notice(notice_no: str, out_dir: Path) -> NoticeFiles:
    """공고번호로 공고 행을 찾아 정보·면허제한·첨부 파일을 받는다. 못 찾으면 title이 빈 결과."""
    import requests

    nego = _import_nego()  # 경로 설정
    del nego
    from nego import attachments, bidcalc
    from nego import fields as F
    from nego.http_client import ApiConfig, ApiError, DataGoKrClient

    key = service_key()
    if not key:
        raise RuntimeError("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 또는 프로젝트 1 폴더의 .env")
    no = notice_no.strip().upper().split("-")[0]
    client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=30.0, max_retries=1, max_pages=5))
    out = NoticeFiles(no)
    row = None
    for wt in bidcalc.WORK_TYPES:
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[wt],
                                          {"inqryDiv": "2", "bidNtceNo": no}, f"정량평가 공고/{wt}")
        except ApiError as err:
            log.info("공고 조회 실패 %s %s: %s", wt, no, err)
            continue
        row = bidcalc._latest(rows, no)
        if row:
            out.work_type = wt
            break
    if not row:
        return out
    out.ord = str(row.get("bidNtceOrd") or "")
    out.title = str(row.get("bidNtceNm") or "")
    out.institution = str(row.get("dminsttNm") or row.get("ntceInsttNm") or "")
    out.award_method = str(row.get("sucsfbidMthdNm") or "")
    out.contract_method = str(row.get("cntrctCnclsMthdNm") or "")
    out.posted = str(row.get("bidNtceDt") or row.get("rgstDt") or "")[:16]
    out.licenses = bidcalc._licenses(client, row, no, out.ord)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for att in F.extract_attachments(row):
        name = att.get("file_name") or f"첨부{att.get('seq')}"
        if not att.get("url"):
            continue
        try:
            data = attachments.download_bytes(session, att["url"], timeout=60.0)
            out.files += _save(data, name, out_dir, attachments)
        except Exception as err:  # 첨부 하나 못 받아도 계속
            out.skipped.append(f"{name}: {err}")
    return out


# ── 요구 자격 ↔ 회사 자료 ─────────────────────────────────────
_CODE = re.compile(r"\((\d{4}|\d{8}|\d{10})\)\s*$")


def qualification_rows(paths: list[Path], held_codes: set[str], vault=None) -> list[dict]:
    """첨부 참가자격 절의 코드 요건 → [{kind, items:[{name, code, held, docs}]}]. kind: 필수 / 이 중 하나."""
    _import_nego()
    from nego import attachments, qualification_text, qualify

    rows: list[dict] = []
    seen: set[tuple] = set()
    for p in paths:
        ext = p.suffix.lstrip(".").lower()
        if ext not in ("hwp", "hwpx", "pdf", "docx"):
            continue
        try:
            text = attachments.extract_text(p.read_bytes(), ext)
        except Exception:  # 못 읽는 문서는 건너뜀
            continue
        sec = qualification_text.find_qualification_section(text)
        if not sec:
            continue
        res = qualify.evaluate_attachment_text(qualification_text.split_items(sec.body), held_codes)
        groups = [g for g in list(res.missing_groups) + list(res.satisfied_groups) if g.group_no != "held"]
        covered = {c for g in groups for n in g.allowed_names if (c := (_CODE.search(n) or [None, None])[1])}
        # '보유 확인'(held) 묶음은 이미 충족한 단독 요건들 — 다른 묶음에 없는 코드만 한 줄씩 '필수'로
        for g in res.satisfied_groups:
            if g.group_no == "held":
                groups += [type(g)("held", [n]) for n in g.allowed_names
                           if (m := _CODE.search(n)) and m.group(1) not in covered]
        for g in groups:
            items = []
            for name in g.allowed_names:
                m = _CODE.search(name)
                code = m.group(1) if m else ""
                label = name[:m.start()].strip(" :") if m else name
                docs = [str(d.get("서류 이름") or d.get("파일 이름") or "") for d in vault.docs_for_code(code)] if vault and code else []
                items.append({"name": label or code, "code": code, "held": code in held_codes, "docs": docs})
            key = tuple(sorted(i["code"] or i["name"] for i in items))
            if not items or key in seen:
                continue
            seen.add(key)
            rows.append({"kind": "이 중 하나" if len(items) > 1 else "필수", "items": items, "source": p.name})
    return rows
