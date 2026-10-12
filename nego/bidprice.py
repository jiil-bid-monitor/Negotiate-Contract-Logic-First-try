"""투찰가격 분석용 과거 개찰 결과 수집 (입찰 유형).

지일이 볼 만한 공고(지금 키워드·예산 필터를 통과한 **입찰** 유형)의 과거 개찰 결과를 모아
사정률(예정가격 ÷ 기초금액)·1순위 투찰률·낙찰하한율과의 차이·자주 만나는 업체를 집계한다.

    python -m nego.bidprice --months 3            # 최근 3개월 게시분
    python -m nego.bidprice --begin 2026-07-01 --end 2026-09-30

공고 목록·필터는 평소 수집(`pipeline.run`)을 그대로 쓰고, 개찰이 끝난 공고마다
낙찰정보서비스를 두 번 부른다(공고번호 조회): 예비가격 상세(예정가격·기초금액·추첨 예가),
참가업체 전체(순위·투찰금액·투찰률). 응답 칸은 2026-10-12 `--verify`(Run #94·#95)로 확인했다 —
다만 예비가격 15개짜리 공고의 행 모양은 아직 못 봐서, 예정가격·기초금액은 행 중 첫 유효값을 쓴다.

결과는 output/bid_history_*.json(공고별) + 콘솔 요약. 저장소(Supabase·sqlite)는 아직 정하지 않았다.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import fields as F
from .config import ConfigError, load_config, redact
from .http_client import ApiError, DataGoKrClient
from .pipeline import run

log = logging.getLogger(__name__)

CATEGORY = "입찰"
# 예비가격 상세는 업무별 오퍼레이션 — 물품은 --verify로 확인 전(이름 추정)
PREPARE_PRICE_OPERATIONS = {
    "용역": F.SCSBID_OPERATIONS["예비가격/용역"],
    "공사": F.SCSBID_OPERATIONS["예비가격/공사"],
    "물품": "getOpengResultListInfoThngPreparPcDetail",
}


def _num(value) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _first(rows: list[dict], key: str) -> float | None:
    for row in rows:
        value = _num(row.get(key))
        if value:
            return value
    return None


@dataclass
class Bidder:
    rank: int | None
    name: str
    bizno: str
    amount: float | None
    rate: float | None
    remark: str


@dataclass
class BidResult:
    notice_no: str
    notice_ord: str
    title: str
    work_type: str
    institution: str
    opened_at: str | None
    lower_limit_rate: float | None
    basis_amount: float | None = None
    planned_price: float | None = None
    drawn_prices: list[float] = field(default_factory=list)
    bidders: list[Bidder] = field(default_factory=list)
    error: str | None = None

    @property
    def assessment_rate(self) -> float | None:
        """사정률(%) = 예정가격 ÷ 기초금액."""
        if self.planned_price and self.basis_amount:
            return round(self.planned_price / self.basis_amount * 100, 4)
        return None

    @property
    def winner(self) -> Bidder | None:
        ranked = [b for b in self.bidders if b.rank is not None and b.rate is not None]
        return min(ranked, key=lambda b: b.rank) if ranked else None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["assessment_rate"] = self.assessment_rate
        winner = self.winner
        data["winner_rate"] = winner.rate if winner else None
        data["bidder_count"] = len(self.bidders)
        return data


def parse_prepare_prices(rows: list[dict]) -> tuple[float | None, float | None, list[float]]:
    """예비가격 상세 행 → (예정가격, 기초금액, 추첨된 예비가격들)."""
    planned = _first(rows, "plnprc")
    basis = _first(rows, "bssamt")
    drawn = [
        price
        for row in rows
        if str(row.get("drwtYn") or "").upper() == "Y" and (price := _num(row.get("bsisPlnprc")))
    ]
    return planned, basis, drawn


def parse_bidders(rows: list[dict]) -> list[Bidder]:
    bidders = []
    for row in rows:
        rank = _num(row.get("opengRank"))
        bidders.append(
            Bidder(
                rank=int(rank) if rank else None,
                name=str(row.get("prcbdrNm") or ""),
                bizno=str(row.get("prcbdrBizno") or ""),
                amount=_num(row.get("bidprcAmt")),
                rate=_num(row.get("bidprcrt")),
                remark=str(row.get("rmrk") or ""),
            )
        )
    return sorted(bidders, key=lambda b: (b.rank is None, b.rank or 0))


def _by_notice(client: DataGoKrClient, base_url: str, operation: str, notice_no: str, label: str) -> list[dict]:
    rows = client.fetch_all_pages(base_url, operation, {"inqryDiv": "2", "bidNtceNo": notice_no}, label)
    return rows


def fetch_result(client: DataGoKrClient, base_url: str, notice) -> BidResult:
    result = BidResult(
        notice_no=notice.notice_no,
        notice_ord=notice.notice_ord,
        title=notice.title,
        work_type=notice.work_type,
        institution=notice.demand_institution or notice.notice_institution or "",
        opened_at=notice.opening_at,
        lower_limit_rate=_num(notice.raw.get("sucsfbidLwltRate")),
    )
    try:
        operation = PREPARE_PRICE_OPERATIONS.get(notice.work_type)
        if operation:
            prices = _by_notice(client, base_url, operation, notice.notice_no, f"예비가격 {notice.notice_no}")
            # 같은 공고번호의 다른 차수가 섞이면 이 공고 차수만
            same_ord = [r for r in prices if str(r.get("bidNtceOrd") or "") in ("", notice.notice_ord)]
            result.planned_price, result.basis_amount, result.drawn_prices = parse_prepare_prices(same_ord or prices)
        bidders = _by_notice(client, base_url, F.OPENING_BIDDERS_OPERATION, notice.notice_no, f"참가업체 {notice.notice_no}")
        same_ord = [r for r in bidders if str(r.get("bidNtceOrd") or "") in ("", notice.notice_ord)]
        result.bidders = parse_bidders(same_ord or bidders)
    except ApiError as err:
        result.error = str(err)[:200]
    return result


def _opened(notice, now: datetime) -> bool:
    text = (notice.opening_at or "").strip()
    try:
        return datetime.strptime(text[:16], "%Y-%m-%d %H:%M") < now
    except ValueError:
        return False


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


def summarize(results: list[BidResult], own_bizno: str | None = None) -> str:
    lines = []
    usable = [r for r in results if not r.error]
    lines.append(f"개찰 결과 {len(results)}건 (조회 실패 {len(results) - len(usable)}건)")
    for work in ("용역", "공사", "물품"):
        group = [r for r in usable if r.work_type == work]
        if not group:
            continue
        lines.append(f"\n[{work}] {len(group)}건")
        rates = [r.assessment_rate for r in group if r.assessment_rate]
        if rates:
            lines.append(
                f"  사정률(예정가격÷기초금액): {len(rates)}건 · 중앙 {statistics.median(rates):.3f}% · "
                f"10~90% 구간 {_pct(rates, 0.1):.3f}~{_pct(rates, 0.9):.3f}%"
            )
        gaps = [
            r.winner.rate - r.lower_limit_rate
            for r in group
            if r.winner and r.lower_limit_rate and r.winner.rate
        ]
        if gaps:
            lines.append(
                f"  1순위 투찰률 − 낙찰하한율: {len(gaps)}건 · 중앙 {statistics.median(gaps):+.3f}%p · "
                f"10~90% 구간 {_pct(gaps, 0.1):+.3f}~{_pct(gaps, 0.9):+.3f}%p"
            )
        counts = [len(r.bidders) for r in group if r.bidders]
        if counts:
            lines.append(f"  참가업체 수: 중앙 {statistics.median(counts):g}곳 · 최대 {max(counts)}곳")
    seen = Counter()
    names = {}
    for r in usable:
        for b in r.bidders:
            if b.bizno:
                seen[b.bizno] += 1
                names[b.bizno] = b.name
    if seen:
        top = ", ".join(f"{names[k]}({n}회)" for k, n in seen.most_common(10))
        lines.append(f"\n자주 만난 업체(참가 횟수): {top}")
    if own_bizno:
        own = [(r, b) for r in usable for b in r.bidders if b.bizno == own_bizno]
        lines.append(f"\n지일 참가 {len(own)}건")
        for r, b in own[:20]:
            rank = f"{b.rank}위" if b.rank else f"순위 없음({b.remark or '사유 미표기'})"
            limit = f" · 하한율 {r.lower_limit_rate}%" if r.lower_limit_rate else ""
            lines.append(f"  {r.opened_at or ''} {r.title[:40]} — {rank} · 투찰률 {b.rate}%{limit} · 참가 {len(r.bidders)}곳")
    return "\n".join(lines)


def collect(config, begin: datetime, end: datetime, now: datetime | None = None) -> list[BidResult]:
    now = now or datetime.now()
    candidates, _stats, _notices = run(config, now=now, categories={CATEGORY}, period=(begin, end))
    targets = [
        c.notice
        for c in candidates
        if c.is_candidate and c.category == CATEGORY and c.notice.kind == "본공고" and _opened(c.notice, now)
    ]
    log.info("입찰 후보 %d건 중 개찰이 지난 공고 %d건 — 개찰 결과 조회", len(candidates), len(targets))
    client = DataGoKrClient(config.api)
    client.config.num_of_rows = 100
    base_url = os.environ.get("SCSBID_BASE_URL", "").strip() or F.SCSBID_BASE_URL
    return [fetch_result(client, base_url, notice) for notice in targets]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="입찰 유형 과거 개찰 결과 수집·집계")
    parser.add_argument("--months", type=int, default=3, help="최근 N개월 게시분 (기본 3)")
    parser.add_argument("--begin", help="시작일 YYYY-MM-DD (--months 대신)")
    parser.add_argument("--end", help="종료일 YYYY-MM-DD (기본 어제)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        config = load_config()
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 2
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = (
        datetime.strptime(args.end, "%Y-%m-%d") if args.end else today - timedelta(days=1)
    ).replace(hour=23, minute=59)
    begin = datetime.strptime(args.begin, "%Y-%m-%d") if args.begin else today - timedelta(days=30 * args.months)
    try:
        results = collect(config, begin, end)
    except ApiError as err:
        print(f"조회 실패: {redact(str(err), [config.api.service_key])}", file=sys.stderr)
        return 1
    own = (os.environ.get("JIIL_BIZNO") or config.held_raw.get("companyBizno") or "").replace("-", "").strip() or None
    print(summarize(results, own))
    config.output_dir.mkdir(parents=True, exist_ok=True)
    path = config.output_dir / f"bid_history_{begin:%Y%m%d}_{end:%Y%m%d}.json"
    path.write_text(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
