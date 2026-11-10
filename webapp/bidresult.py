"""투찰내역의 개찰 결과 — 기록한 공고마다 나라장터 개찰 결과를 받아 지일 순위·1순위 업체·실제 사정률을 채운다(2026-11-06 사용자 요청).

받는 길은 추천 사정률·백테스트와 같은 `nego.bidprice.fetch_result`(예비가격 상세 → 예정가격·기초금액, 참가업체 전체 → 순위·업체·금액·투찰률).
투찰내역 쪽을 열면 결과가 없는 기록(또는 '개찰 전'·'오류'로 남은 지 1시간 지난 기록)만 받고, 한 번 채운 결과는 다시 받지 않는다 — 줄의
'결과 다시 받기'로만. 결과는 `BidLog`의 `result` 칸(서버 PC data/bid_log.json — 지일 투찰 이력, 공개 저장소 X).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

from nego.bidprice import BidResult

KST = timezone(timedelta(hours=9))
RETRY_AFTER = timedelta(hours=1)   # '개찰 전'·'오류'로 남은 기록을 다시 받아 보는 간격
MAX_PER_CALL = 8                   # 한 번에 받는 기록 수 — 공고마다 나라장터를 2~3번 부르므로 화면이 오래 멈추지 않게
WORK_TYPES = ("공사", "용역", "물품")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _now() -> datetime:
    return datetime.now(KST)


def needs_fetch(entry: dict[str, Any], now: datetime | None = None) -> bool:
    """결과를 받아 볼 기록인가 — 없거나, '개찰 전'·'오류'로 남은 지 1시간이 지났으면."""
    res = entry.get("result")
    if not res:
        return True
    if res.get("status") == "완료":
        return False
    try:
        fetched = datetime.fromisoformat(res.get("fetchedAt") or "")
    except ValueError:
        return True
    return (now or _now()) - fetched >= RETRY_AFTER


def summarize(result: BidResult, entry: dict[str, Any], own_bizno: str, now: datetime | None = None) -> dict[str, Any]:
    """개찰 결과 → 화면·저장용 요약. 참가업체가 하나도 없으면 '개찰 전'."""
    out: dict[str, Any] = {"fetchedAt": (now or _now()).isoformat(timespec="seconds")}
    if result.error:
        out.update(status="오류", error=result.error)
        return out
    if not result.bidders:
        out["status"] = "개찰 전"
        return out
    rate = result.assessment_rate
    winner = result.winner
    own_digits = _digits(own_bizno)
    own = next((b for b in result.bidders if own_digits and _digits(b.bizno) == own_digits), None)
    out.update(
        status="완료",
        bidderCount=len(result.bidders),
        rankedCount=sum(1 for b in result.bidders if b.rank is not None),
        plannedPrice=result.planned_price,
        basisAmount=result.basis_amount,
        assessmentRate=rate,
        winner={"name": winner.name, "bizno": winner.bizno, "amount": winner.amount, "rate": winner.rate} if winner else None,
        own=({"rank": own.rank, "amount": own.amount, "rate": own.rate, "remark": own.remark} if own else None),
        # 투찰 업체 전부(11/10 사용자 요청 — '투찰 업체' 버튼으로 봄). BidLog가 기록 파일 밖에 따로 저장
        aValue=result.a_value,   # 하한선 계산에 넣는 A값(투찰 업체 표의 사정률·A값 뺀 투찰률 계산용, 11/10)
        bidders=[{"rank": b.rank, "name": b.name, "ceo": b.ceo, "bizno": b.bizno, "amount": b.amount, "rate": b.rate,
                  "draws": b.draws, "remark": b.remark, **({"own": True} if b is own else {})} for b in result.bidders],
    )
    # 기록한 기준 사정률 − 실제 사정률(양수 = 실제보다 높게 잡음 → 하한선 통과 쪽), 지일 금액 − 1위 금액
    if rate is not None and entry.get("rate") is not None:
        out["rateDiff"] = round(float(entry["rate"]) - rate, 4)
    if own and own.amount is not None and winner and winner.amount is not None:
        out["gapToWinner"] = round(own.amount - winner.amount)
    return out


def fetch_entry(client: Any, base_url: str, entry: dict[str, Any], own_bizno: str,
                fetch: Callable[..., BidResult] | None = None) -> dict[str, Any]:
    """기록 한 줄의 개찰 결과. 업무를 모르면 공사·용역·물품 순으로 시도."""
    if fetch is None:
        from nego.bidprice import fetch_result as fetch
    works = [entry.get("workType")] if entry.get("workType") in WORK_TYPES else list(WORK_TYPES)
    last: BidResult | None = None
    for work in works:
        notice = SimpleNamespace(notice_no=entry.get("noticeNo") or "", notice_ord=entry.get("noticeOrd") or "000",
                                 title=entry.get("title") or "", work_type=work, demand_institution=entry.get("institution") or "",
                                 notice_institution="", opening_at=None, raw={})
        last = fetch(client, base_url, notice)
        if last.bidders or last.error:
            break
    return summarize(last, entry, own_bizno)


def _lookup_times(client: Any, entry: dict[str, Any]) -> dict[str, str]:
    try:
        from nego.bidcalc import notice_times
        return notice_times(client, entry.get("noticeNo") or "", entry.get("workType") or "")
    except Exception:  # noqa: BLE001 — 일시는 보조 정보라 실패해도 결과 받기는 계속
        return {}


def _needs_times(entry: dict[str, Any], status: str | None) -> bool:
    """'개찰 전'인데 투찰 마감을 아직 안 찾아본 기록(11/10 전 기록 — 새 기록은 불러올 때 둘 다 저장)."""
    return status == "개찰 전" and "closingAt" not in entry


def fill_results(log, client: Any, base_url: str, own_bizno: str, ids: list[str] | None = None, force: bool = False,
                 fetch: Callable[..., BidResult] | None = None,
                 times: Callable[[Any, dict[str, Any]], dict[str, str]] | None = None) -> dict[str, Any]:
    """결과가 필요한 기록을 받아 `log`에 저장. ids를 주면 그 기록만(force면 '완료'도 다시).

    예전 기록(11/10 전)은 결과가 '개찰 전'이면 공고에서 개찰 일시·투찰 마감을 찾아 함께 저장 —
    결과를 다시 받을 때(1시간)가 아니어도 바로."""
    now = _now()
    entries = log.entries()
    if ids:
        targets = [e for e in entries if e.get("id") in ids and (force or needs_fetch(e, now))]
    else:
        targets = [e for e in entries if needs_fetch(e, now)]
    done = targets[:MAX_PER_CALL]
    lookup = times or _lookup_times
    for e in done:
        res = fetch_entry(client, base_url, e, own_bizno, fetch)
        log.set_result(e["id"], res)
        if _needs_times(e, res.get("status")):
            log.set_times(e["id"], lookup(client, e))
    done_ids = {e["id"] for e in done}
    pending = [e for e in entries if e.get("id") not in done_ids and (not ids or e.get("id") in ids)
               and _needs_times(e, (e.get("result") or {}).get("status"))]
    for e in pending[:MAX_PER_CALL]:
        log.set_times(e["id"], lookup(client, e))
    return {"fetched": len(done), "left": max(0, len(targets) - len(done))}
