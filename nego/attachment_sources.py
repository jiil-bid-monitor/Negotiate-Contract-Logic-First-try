"""어떤 첨부를 어떤 순서로 읽을지 — 팀원 수집기(narajangter-bid-monitor)의 첨부 고르기를 옮김 (2026-10-19 통합 결정 1번).

- **과업 문서 순서**: 과업지시서 > 제안요청서 > 그 밖의 규격·시방 문서 > 나머지(공고문 등). 공고문도 참가자격 절 때문에
  계속 읽지만, 과업 내용(싱크로율·LLM 근거)은 과업 문서 쪽 글을 쓴다(`task_text`).
  팀원 `src/api/attachments.ts` `isSpecName`·`specRank`.
- **연결된 사전규격 첨부**: 조달청 집행 본공고는 첨부가 공고서 한 장뿐이고 제안요청서는 앞서 올린 사전규격에만 있는 경우가 많다.
  본공고 응답의 `bfSpecRgstNo`(R26BD…)로 사전규격정보서비스를 inqryDiv=2로 조회해 `specDocFileUrl1~`를 받는다.
  팀원 `src/api/linkedPreStandard.ts` (2026-10-13 실측: 제목만으로 싱크로율을 매긴 25건 중 6건).
- **국방전자조달(d2b) 사전규격 첨부**: 군 기관 사전규격은 나라장터에 첨부 0건이고 파일은 d2b 상세 화면에만 있다.
  참조번호 refNo "PS2026UMM40015" → d2b 목록 화면(세션 쿠키) → 목록 JSON → 상세 화면 → 첨부 링크. 첨부는 그 세션 쿠키가 있어야
  받아져서 같은 `requests.Session`으로 받는다. **공식 API가 아니라 화면을 읽는다** — 구조가 바뀌면 빈 목록(예전처럼 "확인 안 됨").
  팀원 `src/api/d2bPreStandard.ts`.

실패는 전부 빈 목록으로 돌려준다 — 부가 정보라 수집을 막지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from . import fields as F

if TYPE_CHECKING:
    from .http_client import DataGoKrClient
    from .models import Notice

log = logging.getLogger(__name__)

# ── 과업 문서 고르기 ────────────────────────────────────────────

# 과업 내용이 담긴 문서의 파일명. 입찰공고문·계약정보요약서·내역서는 행정 절차·금액표뿐이라 과업 문서로 치지 않는다.
_SPEC_RE = re.compile(r"과업|제안요청|요청서|과업내용|시방|규격서")
_NOT_SPEC_RE = re.compile(r"내역서|산출|계약정보요약|낙찰자|유의서|청렴|서식|양식")


def _normalize_name(name: str) -> str:
    return re.sub(r"[_\-.]+", " ", name or "")


def is_spec_name(name: str) -> bool:
    normalized = _normalize_name(name)
    return bool(_SPEC_RE.search(normalized)) and not _NOT_SPEC_RE.search(normalized)


def spec_rank(name: str) -> float:
    """과업지시서 0 > 제안요청서 1 > 그 밖의 과업 문서 2 > 나머지 3. 같은 순위면 ZIP을 뒤로."""
    if is_spec_name(name):
        base = 0 if re.search(r"과업지시|과업내용", name) else 1 if re.search(r"제안요청|요청서", name) else 2
    else:
        base = 3
    return base + (0.5 if name.lower().endswith(".zip") else 0)


def order_attachments(attachments: list[dict[str, str]]) -> list[dict[str, str]]:
    """과업 문서를 앞으로(안정 정렬 — 같은 순위끼리는 원래 순서)."""
    return sorted(attachments, key=lambda a: spec_rank(a.get("file_name", "")))


def is_quantity_sheet(name: str) -> bool:
    """공내역서 — 과업지시서가 없는 공사 공고에서 "무엇을 만드는 공사인지"가 드러나는 엑셀(산출근거·단가산출서는 뺌)."""
    return "내역" in (name or "") and not re.search(r"산출|단가", name or "")


# ── 연결된 사전규격 첨부 ────────────────────────────────────────

_LINKED_PRESPEC_RE = re.compile(r"^R\d{2}BD\d+$")
LINKED_PRESPEC_CACHE = Path("cache") / "linked_prespec.json"


def linked_prespec_no(notice: "Notice") -> str | None:
    if notice.kind != "본공고":
        return None
    no = str((notice.raw or {}).get("bfSpecRgstNo") or "").strip()
    return no if _LINKED_PRESPEC_RE.match(no) else None


def _load_cache(path: Path) -> dict[str, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, data: dict[str, list[str]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as err:
        log.warning("연결 사전규격 캐시를 저장하지 못했습니다: %s", err)


def _lookup_prespec_urls(client: "DataGoKrClient", reg_no: str, work_type: str) -> list[str] | None:
    """None = 조회 실패(캐시하지 않아 다음에 다시 본다). 본공고와 사전규격 업무구분이 다를 수 있어 같은 구분부터 차례로."""
    base = os.environ.get("PRESPEC_BASE_URL", F.PRESPEC_BASE_URL)
    ops = list(dict.fromkeys([F.PRESPEC_OPERATIONS.get(work_type), *F.PRESPEC_OPERATIONS.values()]))
    for op in filter(None, ops):
        try:
            envelope = client._call_with_retry(
                base, op, {"inqryDiv": "2", "bfSpecRgstNo": reg_no, "numOfRows": "5", "pageNo": "1"}, f"연결 사전규격/{reg_no}"
            )
        except Exception as err:  # noqa: BLE001 — 부가 정보, 실패해도 수집 계속
            log.warning("연결 사전규격 조회 실패 %s: %s", reg_no, err)
            return None
        for item in envelope.items:
            if str(item.get("bfSpecRgstNo") or "") == reg_no:
                return [
                    str(item.get(f"{F.PRESPEC_ATTACHMENT_URL_PREFIX}{i}") or "").strip()
                    for i in range(1, 11)
                    if str(item.get(f"{F.PRESPEC_ATTACHMENT_URL_PREFIX}{i}") or "").strip()
                ]
    return []


def linked_prespec_attachments(
    client: "DataGoKrClient | None", notice: "Notice", cache_path: Path = LINKED_PRESPEC_CACHE
) -> list[dict[str, str]]:
    reg_no = linked_prespec_no(notice)
    if not reg_no or client is None:
        return []
    cache = _load_cache(cache_path)
    urls = cache.get(reg_no)
    if urls is None:
        urls = _lookup_prespec_urls(client, reg_no, notice.work_type)
        if urls is None:
            return []
        cache[reg_no] = urls
        _save_cache(cache_path, cache)
    return [
        {"seq": f"사전규격{i}", "file_name": f"연결 사전규격({reg_no}) 첨부 {i}", "url": url, "ext": ""}
        for i, url in enumerate(urls, 1)
    ]


# ── 국방전자조달(d2b) 사전규격 첨부 ─────────────────────────────

D2B = "https://www.d2b.go.kr"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_D2B_REF_RE = re.compile(r"^PS(\d{4})([A-Z0-9]{3})([0-9-]+)$")
_D2B_LINK_RE = re.compile(r'href="(/common/downloadSes\.do\?fileIdxx=[A-Za-z0-9]+)"[^>]*>\s*([^<]+?)\s*<')


def d2b_ref_of(notice: "Notice") -> tuple[str, str, str] | None:
    """나라장터 참조번호(refNo) → (연도, 부서부호, 판단번호). 군 기관 규격이 아니면 None."""
    m = _D2B_REF_RE.match(str((notice.raw or {}).get("refNo") or "").strip())
    return (m.group(1), m.group(2), m.group(3)) if m else None


def parse_d2b_links(html: str) -> list[tuple[str, str]]:
    """상세 화면 HTML → [(파일명, 경로)]."""
    return [(name.replace("&amp;", "&").strip(), path) for path, name in _D2B_LINK_RE.findall(html)]


def _received_at(notice: "Notice") -> datetime:
    text = str((notice.raw or {}).get("rcptDt") or (notice.raw or {}).get("rgstDt") or notice.posted_at or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.now()


def d2b_attachments(session: requests.Session, notice: "Notice", timeout: float = 15.0) -> list[dict[str, str]]:
    """국방전자조달 사전규격 첨부. 같은 `session`으로 받아야 한다(세션 쿠키). 못 찾으면 빈 목록."""
    ref = d2b_ref_of(notice)
    if ref is None:
        return []
    year, dprt, dcsn = ref
    headers = {"User-Agent": _UA}
    try:
        session.get(f"{D2B}/psb/plan/servicePreStdList.do", headers=headers, timeout=timeout)
        at = _received_at(notice)
        rows = session.post(
            f"{D2B}/psb/plan/getPreStdList.json",
            headers=headers,
            data={
                "from_month": (at - timedelta(days=14)).strftime("%Y%m%d"),
                "to_month": (at + timedelta(days=14)).strftime("%Y%m%d"),
                "dcsn_numb": dcsn,
                "exct_divs": "A",
                "currentPageNo": "1",
            },
            timeout=timeout,
        ).json().get("list") or []
        row = next(
            (r for r in rows if r.get("dprtCode") == dprt and r.get("ordrYear") == year and r.get("dcsnNumb") == dcsn), None
        )
        if row is None:
            log.debug("국방전자조달 목록에 없음: %s %s", notice.notice_no, ref)
            return []
        view = session.post(
            f"{D2B}/pdb/plan/preStdView.do",
            headers=headers,
            data={"ordr_year": year, "dprt_code": dprt, "dcsn_numb": dcsn, "lv1_divs": row.get("lv1Divs", "")},
            timeout=timeout,
        )
        if view.status_code >= 400:
            return []
    except (requests.RequestException, ValueError) as err:
        log.debug("국방전자조달 첨부 조회 실패 %s: %s", notice.notice_no, err)
        return []
    return [
        {
            "seq": f"d2b{i}",
            "file_name": name,
            "url": f"{D2B}{path}",
            "ext": name.rsplit(".", 1)[-1].lower() if "." in name else "",
            "referer": f"{D2B}/pdb/plan/preStdView.do",
        }
        for i, (name, path) in enumerate(parse_d2b_links(view.text), 1)
    ]


def attachments_for(
    notice: "Notice", session: requests.Session, client: "DataGoKrClient | None" = None
) -> list[dict[str, str]]:
    """읽을 첨부 전부를 과업 문서 순서로 — 공고 첨부 + 연결 사전규격 첨부 + (첨부 없는 군 기관 사전규격이면) d2b 첨부."""
    items = list(notice.attachments) + linked_prespec_attachments(client, notice)
    if not notice.attachments and notice.kind == "사전규격":
        items += d2b_attachments(session, notice)
    return order_attachments(items)
