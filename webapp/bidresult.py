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


def fill_results(log, client: Any, base_url: str, own_bizno: str, ids: list[str] | None = None, force: bool = False,
                 fetch: Callable[..., BidResult] | None = None) -> dict[str, Any]:
    """결과가 필요한 기록을 받아 `log`에 저장. ids를 주면 그 기록만(force면 '완료'도 다시)."""
    now = _now()
    entries = log.entries()
    if ids:
        targets = [e for e in entries if e.get("id") in ids and (force or needs_fetch(e, now))]
    else:
        targets = [e for e in entries if needs_fetch(e, now)]
    done = targets[:MAX_PER_CALL]
    for e in done:
        log.set_result(e["id"], fetch_entry(client, base_url, e, own_bizno, fetch))
    return {"fetched": len(done), "left": max(0, len(targets) - len(done))}
