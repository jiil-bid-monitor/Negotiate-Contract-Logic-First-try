"""저장된 수집 결과(N 모양) → 팀원 화면(narajangter-bid-monitor `scripts/ui.html`)이 부르는 API 모양.

2026-10-19 통합(화면 이식 B안): 수집·판정·첨부·싱크로율은 지금처럼 이 레포 파이썬(`nego`)이 하고, 화면은 팀원 것을 쓴다.
화면이 기대하는 응답(`/api/notices`·`/api/excluded`·`/api/search`·`/api/corpus`·`/api/project`)을 여기서 만든다.
판정 규칙은 바꾸지 않는다 — 이미 계산해 저장한 값을 옮겨 담기만 한다.

- 공고 하나 = 공고번호(차수 없이) — 참가여부·올리기도 공고번호로 묶인다.
- 관심 공고 / 세부품명 공고(키워드 없이 보유 품목) 구분은 `section`(통합 결정 10·12번).
- 제외된 공고의 "빠진 이유"는 제외 사유 문구("제외키워드 (운영 용역)" 등)를 화면 단계 이름으로 바꾼다.
"""

from __future__ import annotations

import re
from typing import Any

from .sync import BORDER, HIGH

# 제외 사유 머리말 → 화면 단계 이름(ui.html STAGE_LABEL 키)
STAGE_OF = {
    "본공고 게시됨": "본공고 게시",
    "제외키워드": "제외키워드",
    "해외개최": "해외개최",
    "최소예산": "최소예산",
    "미매칭": "키워드·품목",
    "보유면허없음": "참가자격",
    "마감 지남": "마감",
    "지역제한": "참가지역",
}
# 제외된 공고 탭에 안 싣는 단계 — 미매칭은 수천~수만 건이라 목록으로는 쓸모없고(공고번호 검색으로 본다), 마감은 볼 일이 없다
HIDDEN_STAGES = {"키워드·품목", "마감"}
# 세부품명 공고 탭(목록 밖)에 안 싣는 단계 — 금액·마감·본공고 게시는 품목이 맞아도 그대로 거른다(팀원 화면 10/16 규칙)
HELD_SKIP_STAGES = {"최소예산", "마감", "본공고 게시"}
METHOD = {"협상": "협상", "규격가격동시입찰": "규격가격동시", "입찰": "입찰"}


def stage_of(reason: str | None) -> tuple[str, str]:
    """"제외키워드 (운영 용역)" → ("제외키워드", "제목에 제외 키워드 '운영 용역'")."""
    reason = reason or ""
    head, _, rest = reason.partition(" (")
    detail = rest[:-1] if rest.endswith(")") else rest
    stage = STAGE_OF.get(head.strip(), head.strip() or "기타")
    if stage == "제외키워드":
        return stage, f"제목에 제외 키워드 '{detail}'"
    return stage, reason


def _bid_deadline(c: dict[str, Any]) -> str | None:
    for label, iso in c.get("deadlines") or []:
        if label in ("입찰 마감", "의견등록 마감"):
            return iso
    return c.get("deadline")


def _steps(c: dict[str, Any], held_names: dict[str, str]) -> list[dict[str, Any]]:
    """조건별 확인 결과 — 아는 것만. 빠진 단계 + 키워드·품목 + 참가자격 + 참가지역."""
    steps: list[dict[str, Any]] = []
    stage, detail = stage_of(c.get("excluded_reason")) if not c.get("is_candidate") else (None, "")
    if stage and stage not in ("키워드·품목", "참가자격", "참가지역"):
        steps.append({"step": stage, "ok": False, "detail": detail})
    kws = c.get("interest_keywords") or c.get("matched_keywords") or []
    prods = [p for p in c.get("product_codes") or [] if p in held_names]
    parts = [f"[공고 제목] '{k}' — 우리 키워드" for k in kws]
    parts += [f"[나라장터 물품분류] {held_names[p]}({p}) — 지일 등록 품목" for p in prods]
    if stage == "키워드·품목":
        # 무엇이 안 맞았는지 이름으로 — 용역 공고는 세부품명 칸에 용역분류 이름이 든다(fields.product_class_name)
        name = c.get("product_name")
        what = "용역분류" if c.get("work_type") == "용역" else "세부품명"
        # what·name은 화면 팝업이 항목별로 나눠 그릴 때 쓴다(index.html productMissTip)
        steps.append({"step": "키워드·품목", "ok": False, "what": what, "name": name or "",
                      "detail": f"공고명·품명에 관심 키워드가 없고 {what}({name})도 지일 등록 품목이 아님" if name
                      else "공고명에 관심 키워드가 없고 공고에 세부품명번호도 없음"})
    else:
        steps.append({"step": "키워드·품목", "ok": bool(parts), "detail": " · ".join(parts) or "키워드·품목 없음"})
    q = c.get("qualification") or {}
    if stage == "참가자격":
        steps.append({"step": "참가자격", "ok": False, "detail": detail})
    elif c.get("kind") == "사전규격":
        steps.append({"step": "참가자격", "ok": True, "detail": "사전규격 — 나라장터 업종제한 정보 없음(첨부로만 확인)"})
    elif not q.get("checked"):
        steps.append({"step": "참가자격", "ok": True, "detail": "업종제한 없음"})
    elif not q.get("missing"):  # passes는 미보유 1개까지 통과(후보 선정용)라 충족 표시엔 안 씀
        steps.append({"step": "참가자격", "ok": True, "detail": f"충족 — {q.get('summary') or ''}".rstrip(" —")})
    else:
        steps.append({"step": "참가자격", "ok": True, "detail": f"자격 미보유 — {', '.join(q.get('missing') or [])} (공동수급 확인)"})
    r = c.get("region_check") or {}
    if stage == "참가지역":
        steps.append({"step": "참가지역", "ok": False, "detail": detail})
    elif r.get("status") == "충족":
        steps.append({"step": "참가지역", "ok": True, "detail": f"지역제한 {'·'.join(r.get('required') or [])} · 본점 충족"})
    elif c.get("kind") == "사전규격":
        steps.append({"step": "참가지역", "ok": True, "detail": "사전규격 — 나라장터 참가가능지역 정보 없음"})
    else:
        steps.append({"step": "참가지역", "ok": True, "detail": "지역제한 없음" if not c.get("regions") else
                      f"참가가능지역 {', '.join(c.get('regions'))}"})
    return steps


def item(c: dict[str, Any], held_names: dict[str, str], promoted: dict[str, Any] | None = None) -> dict[str, Any]:
    """카드 한 장(ui.ts decorate와 noticeSummary를 합친 모양) + 이 레포 판정 자료(qual·sync·지역·확인 필요 칩)."""
    sync = c.get("sync") or {}
    prods = [p for p in c.get("product_codes") or [] if p]
    matched_products = [{"code": p, "name": held_names.get(p, p)} for p in (c.get("matched_product_codes") or [])]
    stage, detail = (None, None) if c.get("is_candidate") else stage_of(c.get("excluded_reason"))
    return {
        "noticeNo": c.get("notice_no"),
        "noticeOrd": c.get("notice_ord"),
        "title": c.get("title"),
        "institution": c.get("notice_institution"),
        "demandInstitution": c.get("demand_institution"),
        "businessType": c.get("work_type"),
        "sourceType": c.get("kind"),
        # 기준 마감 = 자격등록·입찰·의견등록 중 가장 이른 것(통합 결정 4번) — 커서를 대면 deadlines 전부
        "deadline": c.get("deadline"),
        "deadlineKind": c.get("deadline_label"),
        "deadlines": c.get("deadlines") or [],
        "bidDeadline": _bid_deadline(c),
        "budgetAmount": c.get("budget"),
        "budgetKind": c.get("budget_kind"),
        "postedAt": c.get("posted_at"),
        "detailUrl": c.get("detail_url"),
        "productCode": prods[0] if prods else None,
        "productCodes": prods,
        "productName": c.get("product_name"),
        "bidMethod": c.get("award_method"),
        "bidMethodCategory": METHOD.get(c.get("category") or ""),
        "reNotice": bool(c.get("is_re_notice")),
        "confidence": c.get("confidence") or "참고용",
        "section": c.get("section") or "관심",
        "matchedBy": {"products": matched_products, "industries": [], "keywords": c.get("matched_keywords") or [],
                      "classes": []},
        "interestKeywords": c.get("interest_keywords") or [],
        "reviewExclude": c.get("review_exclude"),
        "overseasSuspect": c.get("overseas_suspect"),
        "overseasAttachment": c.get("overseas_attachment"),
        "overseas": c.get("overseas_mongolia"),
        "regions": c.get("regions") or [],
        "regionCheck": c.get("region_check") or {},
        "regionText": c.get("region_text"),
        "regionSuspect": c.get("region_suspect"),
        "joint": c.get("joint") or {},
        "qual": c.get("qualification") or {},
        "textFlags": c.get("text_flags") or [],
        "linkedSystem": c.get("linked_system"),
        "linkedBidNotices": c.get("linked_bid_notices") or [],
        "attachmentCount": c.get("attachment_count"),
        "hasAttachmentText": bool(c.get("has_attachment_text")),
        "scopeItems": c.get("scope_items") or [],
        "sync": sync,
        "maxScore": sync.get("score") or 0,
        "basis": "제목+과업내용" if sync.get("basis") == "과업 원문" else "제목",
        "promoted": promoted,
        "excludedAt": stage,
        "excludedDetail": detail,
        "steps": _steps(c, held_names),
    }


def held_products(held_raw: dict[str, Any]) -> list[dict[str, str]]:
    return [{"code": str(p.get("code") or "").strip(), "name": p.get("name") or ""}
            for p in (held_raw or {}).get("heldProducts", []) or [] if p.get("code")]


def notices(run: dict[str, Any] | None, promoted: dict[str, Any], held_raw: dict[str, Any],
            doc_products: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """`/api/notices` — 목록(items) · 검토필요(titleExcludedItems) · 세부품명 공고 탭의 목록 밖 공고(heldItems).

    `doc_products`: 공고문 참가자격에서 찾은 지일 등록 세부품명 {공고번호: [코드]} (webapp/docscan.py) — '세부품명 미달'
    용역 공고 중 찾은 것도 세부품명 공고 탭에 싣는다(`fromDoc`, 2026-10-20 팀원 레포 10/19 이식)."""
    hp = held_products(held_raw)
    names = {p["code"]: p["name"] for p in hp}
    base = {"heldProducts": hp, "thresholds": {"high": HIGH, "border": BORDER}}
    if not run:
        return {**base, "fetchedAt": None, "period": None, "periodLabel": "", "items": [], "titleExcludedItems": [],
                "heldItems": [], "collected": None, "error": None, "empty": True}
    payload = run
    cands = payload.get("candidates") or []
    rejected = payload.get("rejected") or []
    items, review, held = [], [], []
    for c in cands:
        no = c.get("notice_no")
        if c.get("confidence") == "검토필요" and no not in promoted:
            review.append({**item(c, names), "excludeKeyword": c.get("review_exclude") or ""})
            continue
        items.append(item(c, names, promoted.get(no)))
    for c in rejected:
        no = c.get("notice_no")
        if no in promoted:
            items.append(item(c, names, promoted[no]))
            continue
        stage, _ = stage_of(c.get("excluded_reason"))
        doc_codes = [p for p in (doc_products or {}).get(no) or [] if p in names]
        if stage == "키워드·품목" and c.get("only_keyword_miss") and doc_codes:
            row = item(c, names)
            row["matchedBy"]["products"] = [{"code": p, "name": names[p]} for p in doc_codes]
            row["matchedBy"]["keywords"] = []
            held.append({**row, "outside": True, "fromDoc": True, "productCode": doc_codes[0], "productName": names[doc_codes[0]]})
            continue
        if stage not in HELD_SKIP_STAGES and any(p in names for p in c.get("product_codes") or []):
            row = item(c, names)
            row["matchedBy"]["products"] = [{"code": p, "name": names[p]} for p in c.get("product_codes") or [] if p in names]
            row["matchedBy"]["keywords"] = []
            held.append({**row, "outside": True})
    # 검토필요 공고도 세부품명이 맞으면 세부품명 공고 탭에 함께(팀원 화면 10/16)
    for r in review:
        if any(p in names for p in r["productCodes"]):
            held.append({**r, "outside": True, "excludedAt": "제외키워드",
                         "excludedDetail": f"제목에 제외 키워드 '{r['excludeKeyword']}'",
                         "matchedBy": {**r["matchedBy"], "keywords": [],
                                       "products": [{"code": p, "name": names[p]} for p in r["productCodes"] if p in names]}})
    stats = payload.get("stats") or {}
    params = run.get("params") or {}
    days = params.get("days")
    label = f"최근 {days}일" if days else f"{params.get('begin')} ~ {params.get('end')}"
    return {
        **base,
        "runId": run.get("id"),
        "format": payload.get("format"),
        "fetchedAt": run.get("finished_at") or run.get("started_at"),
        "period": str(days) if days else None,
        "periodBegin": stats.get("period_begin"),
        "periodEnd": stats.get("period_end"),
        "periodLabel": label,
        "items": items,
        "titleExcludedItems": review,
        "heldItems": held,
        "collected": {"bid": max(0, (stats.get("fetched") or 0) - (stats.get("prespec_fetched") or 0)),
                      "pre": stats.get("prespec_fetched") or 0},
        "prespecError": stats.get("prespec_error"),
        "keywords": payload.get("keywords") or [],
        "error": None,
    }


def excluded(run: dict[str, Any] | None, promoted: dict[str, Any], held_raw: dict[str, Any]) -> dict[str, Any]:
    """`/api/excluded` — 우리 분야 후보였다가 빠진 공고(미매칭·마감 지난 공고는 뺌)."""
    if not run:
        return {"error": "먼저 '실제 공고' 탭에서 나라장터 공고를 불러오세요."}
    names = {p["code"]: p["name"] for p in held_products(held_raw)}
    payload = run
    # 키워드·품목 단계는 다른 조건은 모두 통과한 공고(only_keyword_miss)만 '세부품명 미달'로 싣는다 — 다른 조건에도 걸린
    # 미매칭은 수천 건이라 싣지 않는다(2026-10-20 팀원 레포 10/19 이식, 기본 '전체' 보기에 함께)
    rows = [item(c, names) for c in payload.get("rejected") or [] if c.get("notice_no") not in promoted]
    keep = {c.get("notice_no") for c in payload.get("rejected") or [] if c.get("only_keyword_miss")}
    rows = [r for r in rows if r["excludedAt"] not in HIDDEN_STAGES or (r["excludedAt"] == "키워드·품목" and r["noticeNo"] in keep)]
    by_stage: dict[str, int] = {}
    for r in rows:
        by_stage[r["excludedAt"]] = by_stage.get(r["excludedAt"], 0) + 1
    stats = payload.get("stats") or {}
    params = run.get("params") or {}
    return {
        "periodLabel": f"최근 {params['days']}일" if params.get("days") else f"{params.get('begin')} ~ {params.get('end')}",
        "fetchedAt": run.get("finished_at") or run.get("started_at"),
        "total": stats.get("fetched") or 0,
        "byStage": by_stage,
        "items": rows,
        "keywords": payload.get("keywords") or [],
    }


NOTICE_NO_RE = re.compile(r"^[A-Za-z]\d{2}[A-Za-z]{2}\d{6,}(-\d+)?$|^\d{11,}(-\d+)?$")


def _past_rows(past, top: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in top:
        p = past.projects[t["index"]] if 0 <= t.get("index", -1) < len(past.projects) else {}
        out.append({"id": t.get("index"), "name": t.get("title"), "year": t.get("year"), "shown": t.get("score"),
                    "budgetAmount": None, "shared": t.get("shared") or [], "excerpt": project_excerpt(p)})
    return out


def project_excerpt(p: dict[str, Any]) -> str | None:
    parts = []
    if p.get("overview"):
        parts.append(f"과업: {p['overview']}")
    if p.get("exhibition"):
        parts.append(f"전시 내용: {p['exhibition']}")
    return "\n\n".join(parts) or None


def search(q: str, run: dict[str, Any] | None, past, held_raw: dict[str, Any]) -> dict[str, Any]:
    """`/api/search` — 공고번호면 이번 수집 결과에서 찾아(첨부 과업으로 매긴 싱크로율 그대로) 목록 여부·빠진 이유와 함께,
    아니면 제목으로 과거 실적과 비교."""
    q = q.strip()
    if not q:
        return {"maxScore": 0, "top": []}
    payload = run or {}
    known = {str(c.get("notice_no") or "").lower() for c in (payload.get("candidates") or []) + (payload.get("rejected") or [])}
    if NOTICE_NO_RE.match(q) or q.split("-")[0].lower() in known:
        if not run:
            return {"maxScore": 0, "top": [], "notice": None, "noticeMissing": "notLoaded"}
        needle = q.split("-")[0].lower()
        payload = run
        found = next((c for c in (payload.get("candidates") or []) + (payload.get("rejected") or [])
                      if str(c.get("notice_no") or "").lower().startswith(needle)), None)
        if not found:
            return {"maxScore": 0, "top": [], "notice": None, "noticeMissing": "notFound"}
        names = {p["code"]: p["name"] for p in held_products(held_raw)}
        row = item(found, names)
        sync = found.get("sync") or {}
        top = sync.get("top") or []
        if not top and getattr(past, "available", True):
            sync = past.score(found.get("title") or "", top=10)
            top = sync.get("top") or []
        return {"maxScore": sync.get("score") or 0, "basis": row["basis"], "notice": row, "sync": sync,
                "top": _past_rows(past, top)}
    sync = past.score(q, top=10)
    return {"maxScore": sync.get("score") or 0, "basis": "제목", "sync": sync, "top": _past_rows(past, sync.get("top") or []),
            "error": past.error if not getattr(past, "available", True) else None}


def corpus(past) -> dict[str, Any]:
    projects = [{"id": i, "name": p.get("title"), "year": p.get("year"), "budgetAmount": None,
                 "hasBody": bool(p.get("overview") or p.get("exhibition"))} for i, p in enumerate(past.projects)]
    years = sorted({p["year"] for p in projects if p["year"]})
    return {
        "total": len(projects), "withBody": sum(p["hasBody"] for p in projects), "withBudget": 0,
        "range": f"{years[0]}–{years[-1]}" if years else "", "projects": projects, "error": past.error,
    }
