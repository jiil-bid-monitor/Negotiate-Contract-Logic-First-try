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
_ANY_CODE = re.compile(r"(?<!\d)(\d{4}|\d{8}|\d{10})(?!\d)")

# 코드 없이 서류 이름으로만 적힌 참가자격 증빙(11/04 사용자 요청 B — 필암서원 7) "중소기업·소상공인확인서를 소지한 업체").
# (보일 이름, 참가자격 문장에서 찾을 글, 회사 자료 파일·서류 이름에서 찾을 낱말). 띄어쓰기 무시.
NAME_REQS: list[tuple[str, str, tuple[str, ...]]] = [
    ("중소기업·소상공인 확인서", r"(?:중소기업|소상공인)[·ㆍ\s()/]*(?:소상공인)?\)?\s*확인서",
     ("중소기업확인서", "소상공인확인서", "중소기업(소상공인)확인서", "중소기업·소상공인확인서")),
    ("여성기업 확인서", r"여성기업\s*(?:확인서|증명)", ("여성기업",)),
    ("장애인기업 확인서", r"장애인기업\s*(?:확인서|증명)", ("장애인기업",)),
    ("사회적기업 인증서", r"사회적기업\s*인증", ("사회적기업",)),
    ("직접생산확인증명서", r"직접\s*생산\s*확인", ("직접생산",)),
]


def _nego_config() -> Path | None:
    """프로젝트 1의 config 폴더(등록증·이름 사전) — 웹앱 수집과 같은 이름 자료를 쓰려고."""
    repo = nego_repo()
    return repo / "config" if repo and (repo / "config").is_dir() else None


def _names() -> tuple[dict[str, str], dict[str, str]]:
    """(등록증 이름, 코드 이름 사전) — 웹앱 수집(`nego.config.load_config`)과 같은 방식."""
    import json

    from nego import qualify

    cfg = _nego_config()
    if not cfg:
        return {}, {}

    def read(name: str) -> dict:
        try:
            return json.loads((cfg / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    held, codes = read("held_qualifications.json"), read("codes.json")
    return (qualify.load_held_code_names(held),
            qualify.load_code_names({**codes, "productCodes": held.get("heldProducts", [])}, read("code_names.json")))


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def _vault_paths(vault, docs: list[dict]) -> list[str]:
    """서류상태 줄의 '파일 이름' → 묶음 안 경로(files/…)."""
    out = []
    for d in docs:
        name = str(d.get("파일 이름") or "").strip().replace("\\", "/")
        if not name:
            continue
        for k in (name, f"files/{name}"):
            if k in vault.files:
                out.append(k)
                break
        else:
            tail = [k for k in vault.files if k.rsplit("/", 1)[-1] == name.rsplit("/", 1)[-1]]
            out += tail[:1]
    return list(dict.fromkeys(out))


def _merge_or(groups: list[list[str]]) -> list[list[str]]:
    """문서끼리 다르게 적은 '또는' 묶음(공고문 4442|4444, 제안요청서 4442|4444|6484)이 코드가 겹치면 하나로."""
    out: list[list[str]] = []
    for g in groups:
        codes = {_code(n) for n in g} - {""}
        for o in out:
            if codes & ({_code(n) for n in o} - {""}):
                o += [n for n in g if _code(n) not in {_code(x) for x in o}]
                break
        else:
            out.append(list(g))
    return out


def _code(name: str) -> str:
    m = _CODE.search(name)
    return m.group(1) if m else ""


def qualification_rows(paths: list[Path], held_codes: set[str], vault=None, licenses: list[str] | None = None) -> list[dict]:
    """첨부 참가자격 → [{kind, items:[{name, code, held, docs, paths, need}], source}].

    웹앱 수집(`nego.attachments.save_attachment_texts`)과 같은 판정: 모든 첨부의 참가자격 항목을 모아 한 번에
    `evaluate_attachment_text`(등록증 이름·코드 이름 사전 포함)로 본다(11/04 사용자 요청 A). 문서끼리 겹치는 '또는' 묶음은 합치고,
    나라장터 면허제한(API) 업종 중 첨부에 없는 것은 따로 한 줄. 코드 없이 이름만 적힌 증빙(중소기업확인서 등)도 한 줄씩(요청 B).
    kind: 필수 / 이 중 하나 / 나라장터 면허제한 / 필수 서류."""
    _import_nego()
    from nego import attachments, qualification_text, qualify

    per_doc: list[tuple[str, list[str]]] = []
    for p in paths:
        ext = p.suffix.lstrip(".").lower()
        if ext not in ("hwp", "hwpx", "pdf", "docx"):
            continue
        try:
            from .notice import doc_text  # PDF 글이 빠진 쪽은 OCR로(공고 읽기와 같은 글, 한 번만 읽음)
            text = doc_text(p)
        except Exception:  # 못 읽는 문서는 건너뜀
            continue
        sec = qualification_text.find_qualification_section(text)
        if sec and sec.items:
            per_doc.append((p.name, list(sec.items)))
    all_items = [i for _, items in per_doc for i in items]
    held_names, code_names = _names()

    def sources(codes: set[str], words: tuple[str, ...] = ()) -> str:
        hit = [n for n, items in per_doc if any(
            codes & set(_ANY_CODE.findall(i)) or any(_sq(w) in _sq(i) for w in words) for i in items)]
        return ", ".join(hit)

    def item(name: str) -> dict:
        code = _code(name)
        label = name[:_CODE.search(name).start()].strip(" :") if code else name
        docs = vault.docs_for_code(code) if vault and code else []
        return {"name": label or code, "code": code, "held": code in held_codes,
                "docs": [str(d.get("서류 이름") or d.get("파일 이름") or "") for d in docs],
                "paths": _vault_paths(vault, docs) if vault else []}

    rows: list[dict] = []
    if all_items:
        res = qualify.evaluate_attachment_text(all_items, held_codes, held_names, code_names, {})
        groups = [list(g.allowed_names) for g in list(res.missing_groups) + list(res.satisfied_groups) if g.group_no != "held"]
        groups = _merge_or(groups)
        covered = {_code(n) for g in groups for n in g}
        # '보유 확인'(held) 묶음은 이미 충족한 단독 요건들 — 다른 묶음에 없는 코드만 한 줄씩 '필수'로
        for g in res.satisfied_groups:
            if g.group_no == "held":
                groups += [[n] for n in g.allowed_names if _code(n) and _code(n) not in covered]
        lines = [ln for i in all_items for ln in i.splitlines()]
        for g in groups:
            items = [item(n) for n in g]
            for it in items:
                # 세부품명(직접생산) — 문장에 직접생산확인증명서가 함께 적혔으면 표시
                if len(it["code"]) >= 8 and any(it["code"] in ln and re.search(r"직접\s*생산", ln) for ln in lines):
                    it["need"] = "직접생산확인증명서"
            rows.append({"kind": "이 중 하나" if len(items) > 1 else "필수", "items": items,
                         "source": sources({i["code"] for i in items})})

    # 나라장터 면허제한(API) — 첨부에 이미 있는 업종은 빼고(이름 대조)
    known = {_sq(re.sub(r"\(.*$", "", it["name"])) for r in rows for it in r["items"]}
    known |= {_sq(re.sub(r"\(.*$", "", (held_names.get(it["code"]) or code_names.get(it["code"]) or "")))
              for r in rows for it in r["items"]}
    by_name = {_sq(n): c for c, n in {**code_names, **held_names}.items()}
    for lic in licenses or []:
        if _sq(lic) in known or not lic.strip():
            continue
        code = by_name.get(_sq(lic), "")
        it = item(f"{lic}({code})" if code else lic)
        rows.append({"kind": "나라장터 면허제한", "items": [it], "source": "나라장터 면허제한정보"})
        known.add(_sq(lic))

    # 코드 없이 이름만 적힌 증빙 서류
    has_product = any(len(it["code"]) >= 8 for r in rows for it in r["items"])
    for label, pat, words in NAME_REQS:
        if label == "직접생산확인증명서" and has_product:
            continue  # 세부품명 줄에 '직접생산확인증명서'로 표시됨(11/04 필암서원: PDF 공고문 줄바꿈으로 14개 증명서가 다 들어갔음)
        found = [n for n, items in per_doc if any(
            re.search(pat, ln) and not (label == "직접생산확인증명서" and _ANY_CODE.search(ln))  # 코드가 있으면 위 품목 줄에 표시
            for i in items for ln in i.splitlines())]
        if not found:
            continue
        keys = [_sq(w) for w in words]
        docs = [d for d in (vault.docs if vault else [])
                if any(k in _sq(f"{d.get('서류 이름') or ''} {d.get('파일 이름') or ''}") for k in keys)
                and str(d.get("상태") or "").strip() != "재발급"]
        files = _vault_paths(vault, docs) if vault else []
        files += [f for f in (vault.files if vault else {}) if f.startswith("files/")
                  and any(k in _sq(f.rsplit("/", 1)[-1]) for k in keys) and f not in files]
        rows.append({"kind": "필수 서류", "source": ", ".join(found),
                     "items": [{"name": label, "code": "", "held": bool(files or docs), "keys": keys,
                                "docs": [str(d.get("서류 이름") or d.get("파일 이름") or "") for d in docs]
                                or [f.rsplit("/", 1)[-1] for f in files], "paths": files}]})
    return rows
