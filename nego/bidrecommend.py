"""추천 사정률 — 과거 개찰 결과로 "어느 사정률로 투찰했으면 1순위였나"를 세어 투찰가를 추천한다(2026-10-28 사용자 결정: 과거 개찰 통계 기반).

쓰는 자료(서버 PC `output/`, 저장소엔 안 올림):
  - `bid_history_*.json` — `python -m nego.bidprice`(필터 통과 입찰 공고의 개찰 결과)
  - `bid_results.jsonl` — `scripts/fetch_bid_results.py`(공고번호 목록의 개찰 결과 원본 행)

계산 (사정률 r = 우리가 가정하는 예정가격 ÷ 기초금액 × 100):
  우리 투찰금액 = A + (기초금액 × r − A) × 하한율. 과거 공고의 실제 사정률을 s, 예정가격을 P라 하면
  - **유효(하한선 이상)** ⇔ 기초금액 × r ≥ P ⇔ r ≥ s — A값과 상관없다.
  - **1순위** ⇔ 유효 + 우리 투찰률(투찰금액 ÷ P) < 실제 1순위 투찰률 t₁ ⇔ s ≤ r < s × (t₁ − a(1 − 하한율)) ÷ 하한율 (a = A ÷ P).
  공고마다 "1순위였을 r 구간"을 구해 겹치는 곳을 세고, 가장 많이 겹친 r을 추천한다. 순위는 금액만 본 개찰 순위(적격심사 서류 심사 전).

한계(화면에도 적음): 과거 공고의 A값을 모르면 0으로 계산해 이기는 구간이 실제보다 넓게 잡힌다. 참가 100곳 이상 대규모 경쟁은
1순위가 하한선 바로 위(+0.003~0.005%p)라 어떤 r도 몇 건밖에 못 이긴다(2026-10-23 지일 이력 분석 — 운의 영역).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .bidfield import classify
from .bidmodels import region_of
from .bidprice import parse_bidders, parse_prepare_prices

log = logging.getLogger(__name__)

GRID_LO, GRID_HI, GRID_STEP = 96.0, 104.0, 0.001  # 사정률 후보(%) — 변동폭 ±3%보다 넉넉히
MIN_SAMPLES = 10  # 이보다 적으면 조건을 하나씩 풀어 비슷한 공고를 더 모은다
LARGE_COMPETITION = 100  # 참가 100곳 이상 = 대규모 경쟁(2026-10-23 분석 기준)
SCALES = {"all": "전체", "small": f"{LARGE_COMPETITION}곳 미만", "large": f"{LARGE_COMPETITION}곳 이상"}
MIN_PICK_TRIED = 20  # 추천 투찰금액을 고를 때 검증 건수가 이보다 적은 방식은 믿지 않는다
HISTORY_PATTERNS = ("bid_history_*.json", "bid_results*.jsonl")
FIXED_RATE = 100.55  # 고정 사정률 비교값 — 2020~24 지일 이력 중 참가 100곳 미만 ±3% 공고로 고른 값(10/23 분석, 10/28 검증에서 소규모 1순위 최다)
OWN_NAME = "지일 실제 투찰(외주)"  # bidbacktest.OWN과 같은 이름
TOP_N = 5  # 투찰 경향은 1~5위 업체까지 본다(2026-10-28 사용자 결정)
PICK_MIN_VALID = 0.60  # 추천 투찰금액의 조건 — 검증 하한선 통과 비율이 이보다 낮은 방식은 고르지 않음(10/29 사용자 결정, 값은 첨삭 대기)
PICK_MIN_WIN = 0.10  # 검증 1순위 비율이 이보다 낮으면 방식 간 차이를 운으로 보고 '경향 추천'을 쓴다(10/28 백테스트: 소규모 물품·용역 23~50%, 공사·대규모 0~7%)
TREND_NAME = f"경향 추천(사정률 중앙 + 1~{TOP_N}위 간격)"
TRACK_LIMIT = 400  # 검증 성적은 비슷한 공고 중 최근 이만큼만 채점(화면 응답 시간 — 공고마다 그 전 공고로 추천을 다시 만듦)
MIN_LOWER_RATE = 50.0  # 하한율이 이보다 낮으면(0.01·0.1 — 하한선 없는 입찰에 잘못 들어온 값, 10/29 9월 표본) 하한율 없음으로 본다
RANGE_SLACK = 0.5  # 실제 사정률이 변동폭 ±이만큼(%p) 밖이면 입력 오류로 보고 뺀다(10/29 사용자 결정: 이상치는 거름)
CROWD_NAME = "대규모 추천(사정률 분포 ÷ 경쟁 밀집)"
CROWD_STEP = 0.002  # 대규모 추천 계산 격자(%p)
CROWD_HALF_LIFE = 730  # 실제 사정률 분포를 만들 때 이 날수만큼 지난 공고는 무게 절반(최근 경향에 무게 — 2026 ±3% 공고 변화)
CROWD_BW_COMPETITOR = 0.02  # 경쟁업체 가정 사정률 분포를 매끄럽게 하는 폭(%p)
CROWD_MAX = 600  # 대규모 추천은 비슷한 공고 중 최근 이만큼만 씀(오래된 공고는 무게도 작고, 검증 때 계산 시간)
LARGE_MAX_WINDOW = 0.1  # 참가 100곳 이상인데 1순위 구간이 이보다 넓으면(%p, 정상 중앙 0.004) 하한선이 틀린 공고로 보고 1순위·순위 세기에서 뺀다
# (10/29 R 공고 63건 비교: '과거 1순위 최다'가 통신·전기 20건 내내 98.595% — 하한선이 틀려 구간이 넓게 잡힌 몇 건이 겹쳐 최다가 됨)


@dataclass
class PastBid:
    """과거 개찰 1건 — 추천 계산에 필요한 값만."""

    notice_no: str
    work: str
    basis: float
    planned: float
    lower_rate: float  # %
    winner_rate: float  # 1순위 투찰금액 ÷ 예정가격 × 100
    bidder_count: int
    range_width: int | None = None  # 변동폭 ±2 → 2, ±3 → 3, 모르면 None
    a_value: float | None = None
    title: str = ""
    opened_at: str = ""
    source: str = ""
    own_amount: float | None = None  # 지일 투찰금액(참가했을 때) — 검증(`nego.bidbacktest`)에서 외주 추천과 비교
    own_rank: int | None = None  # 지일 개찰 순위(하한 미달 등으로 순위가 없으면 None)
    floor_est: float | None = None  # 참가업체 금액으로 가늠한 하한선(A값을 모를 때) — `_estimate_floor`
    odd_remarks: Counter = field(default_factory=Counter)  # 하한선 위인데 순위가 없는 업체의 비고 — 아직 반영 안 한 무효 조건 찾기용
    floor_err: float = 0.0  # 가늠한 하한선의 오차(금액, 가늠 구간의 절반) — A값을 모를 때만. 1순위·순위 판정에서 이만큼 보수적으로(`judge_err`)
    net_err: float = 0.0  # 순공사원가 기준선의 오차(금액, 가늠 구간의 절반)
    net_line: float | None = None  # 순공사원가 기준선(금액) — 개찰 결과에서 '순공사원가'로 무효된 업체로 정함. `_net_line`
    top_amounts: list[float] = field(default_factory=list)  # 개찰 1~5위 투찰금액(순위순) — 투찰 경향(`nego.bidtrend`)
    amounts: list[float] = field(default_factory=list)  # 참가업체 전부의 투찰금액(하한 미달 포함) — 대규모 추천의 경쟁 밀집(`crowd_rate`)
    draws: list[tuple[int, float, int]] = field(default_factory=list)  # 예비가격 (번호, 기초금액 대비 %, 선택 횟수) — 추첨 모형(`bidmodels`)
    method: str = ""  # 낙찰자 결정 기준 '조달청'·'행자부' 등(예비가격 행 bidwinrSlctnAplBssCntnts) — 가중 KNN·간격 예측
    institution: str = ""  # 발주(수요)기관 — 가중 KNN·간격 예측의 지역(첫 낱말)

    @property
    def field(self) -> str:
        """공고명 키워드로 나눈 분야(`nego.bidfield`). 공고마다 한 번만 계산해 둠 — 검증이 같은 공고의 분야를 수십만 번 묻는다(10/29 웹 응답 50초)."""
        cached = self.__dict__.get("_field_cache")
        if cached is None or cached[0] != self.title:
            cached = (self.title, classify(self.title))
            self.__dict__["_field_cache"] = cached
        return cached[1]

    @property
    def assessment(self) -> float:
        """실제 사정률(예정가격 ÷ 기초금액). 기준 사정률 r로 낸 금액(`amount_at`)은 r ≥ 이 값이면 통과."""
        return self.planned / self.basis * 100

    @property
    def valid_floor(self) -> float:
        """유효 투찰의 최저 금액 = 하한선과 순공사원가 기준선(개찰 결과로 정함) 중 높은 쪽."""
        return max(self.floor, self.net_line or 0.0)

    @property
    def floor_source(self) -> str:
        """하한선을 무엇으로 정했나: A값(공고 값) / 추정(참가업체 금액) / A=0(둘 다 없음)."""
        if self.a_value is not None:
            return "A값"
        return "추정" if self.floor_est else "A=0"

    @property
    def floor(self) -> float:
        """하한선 금액 = A + (예정가격 − A) × 하한율. A를 모르면 참가업체 금액으로 가늠한 값, 그것도 없으면 A=0."""
        if self.a_value is None and self.floor_est:
            return self.floor_est
        a = self.a_value or 0.0
        return a + (self.planned - a) * self.lower_rate / 100

    def amount_at(self, rate: float) -> float:
        """기준 사정률 rate(예정가격 = 기초 × rate로 봄)로 낼 투찰금액 = 그 예정가격의 하한선과 순공사원가 기준선 중 높은 쪽.
        순공사원가 선은 예정가격에 비례(순공사원가 × 예정가격/기초금액 × 비율)하므로 실제 선 × rate ÷ 실제 사정률 — 비율을 따로 가정하지 않음.
        그래서 순공사원가 조건이 있는 공고도 '실제 사정률 ≤ rate ⇔ 통과'가 그대로 성립(10/29 제보 R25BK00645409)."""
        amount = self.floor + (rate / 100 * self.basis - self.planned) * self.lower_rate / 100
        if self.net_line:
            amount = max(amount, self.net_line * rate / self.assessment)
        return amount

    def rate_for(self, amount: float) -> float:
        """이 공고에 이 금액을 냈을 때의 가정 사정률(`amount_at`의 거꾸로). 금액 − 하한선 = 하한율 × (기초 × r − 예정가격)이라
        A값이 따로 필요 없고, 순공사원가 선이 있으면 그 선 기준 값과 작은 쪽."""
        rate = (self.planned + (amount - self.floor) / (self.lower_rate / 100)) / self.basis * 100
        if self.net_line:
            rate = min(rate, amount / self.net_line * self.assessment)
        return rate

    @property
    def win_ceiling(self) -> float:
        """이 r 미만이면 1순위(이상이면 1순위보다 비쌈)."""
        return self.rate_for(self.winner_rate / 100 * self.planned)

    @property
    def window_ok(self) -> bool:
        """1순위·순위 구간을 믿을 만한가 — 대규모 경쟁인데 구간이 `LARGE_MAX_WINDOW`보다 넓으면 하한선(A값·추정)이 틀린 것으로 본다.
        실제 사정률(유효 판정)은 하한선과 무관해 그대로 쓴다."""
        if self.bidder_count < LARGE_COMPETITION:
            return True
        try:
            return self.win_ceiling - self.assessment <= LARGE_MAX_WINDOW
        except (TypeError, ZeroDivisionError):
            return False

    @property
    def judge_err(self) -> float:
        """판정 기준선(하한선 또는 순공사원가 선) 금액의 오차. A값을 아는 공고는 0.
        (10/29 제보 R25BK00645409·R25BK00715689: A값을 모르는 835곳 공고에서 가늠한 하한선이 실제보다 640원 낮아, 1위보다 635원 비싼 금액을 1순위로 셈 —
        A값을 모르면 1위 금액이 곧 가늠 구간의 위 끝이라 1순위는 확정할 수 없음)"""
        if self.net_line and self.net_line > self.floor:
            return self.net_err
        return self.floor_err if (self.a_value is None and self.floor_est) else 0.0

    def sure_ceiling(self, k: int | None = None) -> float:
        """판정용 상한 — 기준선 오차만큼 보수적으로 낮춘 1순위 상한(k를 주면 k위 안 상한). 이 r 미만이면 오차를 감안해도 1순위(k위 안)."""
        top = self.win_ceiling if k is None else self.rank_ceiling(k)
        err = self.judge_err
        if not err or top == float("inf"):
            return top
        amount = self.amount_at(top)
        return top - (self.rate_for(amount) - self.rate_for(amount - err))

    def rank_ceiling(self, k: int = TOP_N) -> float:
        """이 r 미만이면 k위 안(그 공고 k위 업체보다 싸게 씀). 순위 있는 업체가 k곳이 안 되면 하한선만 넘으면 k위 안."""
        if len(self.top_amounts) < k:
            return float("inf")
        return self.rate_for(self.top_amounts[k - 1])


def _num(value: Any) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def range_width(prices: Iterable[float], basis: float | None) -> int | None:
    """예비가격 15개(또는 10개)가 기초금액에서 얼마나 벌어졌는지로 변동폭을 가늠한다 — 2%를 넘는 값이 하나라도 있으면 ±3,
    전부(10개 이상) 2% 안이면 ±2. 추첨된 4개만 있으면 판단하지 않는다(±3 공고도 4개는 2% 안일 수 있음)."""
    prices = [p for p in prices if p]
    if not basis or not prices:
        return None
    spread = max(abs(p / basis - 1) * 100 for p in prices)
    if spread > 2.0001:
        return 3
    return 2 if len(prices) >= 10 else None


def _width_from_rates(lo: float | None, hi: float | None) -> int | None:
    if lo is None or hi is None:
        return None
    width = round(max(abs(lo), abs(hi)))
    return width if width in (2, 3) else None


def _winner_rate(bidders: list, planned: float) -> float | None:
    """1순위(개찰 순위 1 = 하한선 이상 중 가장 낮은 금액)의 투찰률. 금액이 있으면 금액 ÷ 예정가격으로 직접 계산한다."""
    ranked = [b for b in bidders if b.rank is not None]
    if not ranked:
        return None
    first = min(ranked, key=lambda b: b.rank)
    if first.amount and planned:
        return first.amount / planned * 100
    return first.rate


def _estimate_floor(bidders: list, planned: float) -> tuple[float, float] | None:
    """A값을 모를 때 하한선 가늠: 순위가 있는(하한선 이상) 가장 낮은 금액과, 그보다 낮은데 순위가 없는(하한 미달) 가장 높은 금액의
    가운데. 참가업체가 많으면 둘 사이가 아주 좁다. 비고에 '미달'이 적힌 업체가 있으면 그 업체들만 하한 미달로 본다
    (서류 미비 등 다른 사유로 빠진 업체를 섞지 않게)."""
    ranked = [b.amount for b in bidders if b.rank is not None and b.amount]
    if not ranked or not planned:
        return None
    low_ok = min(ranked)
    below = [b for b in bidders if b.rank is None and b.amount and b.amount < low_ok]
    marked = [b for b in below if "미달" in (b.remark or "")]
    below = marked or below
    if not below:
        return None
    hi = max(b.amount for b in below)
    return (hi + low_ok) / 2, (low_ok - hi) / 2  # (가늠값, 오차 = 구간의 절반)


def _net_line(bidders: list, rec: PastBid) -> tuple[float, float] | None:
    """순공사원가 기준선(금액) — 개찰 결과 비고에 '순공사원가'가 적힌 무효 업체 중 가장 높은 금액과, 그보다 높은 순위 있는 업체 중
    가장 낮은 금액의 가운데. 비율(98% 등)을 정해 두지 않고 그 공고 실제 개찰 결과로만 정한다(10/29 사용자 결정 — 고정 조건 아님).
    그런 업체가 없거나 선이 하한선보다 낮으면 None."""
    bad = [b.amount for b in bidders if b.rank is None and b.amount and "순공사" in (b.remark or "")]
    if not bad:
        return None
    hi_bad = max(bad)
    above = [b.amount for b in bidders if b.rank is not None and b.amount and b.amount > hi_bad]
    if not above:
        return None
    line, err = (hi_bad + min(above)) / 2, (min(above) - hi_bad) / 2
    return (line, err) if line > rec.floor else None


def _clean_bizno(value: str | None) -> str:
    return (value or "").replace("-", "").strip()


def _outlier(basis: float, planned: float, width: int | None) -> bool:
    """실제 사정률이 변동폭(모르면 ±3) ± RANGE_SLACK 밖 — 예정가격·기초금액 입력 오류로 본다."""
    w = (width or 3) + RANGE_SLACK
    return abs(planned / basis * 100 - 100) > w


def _make(notice_no: str, work: str, basis, planned, lower, bidders: list, prices: list[float], own_bizno: str = "",
          **extra) -> PastBid | None:
    basis, planned, lower = _num(basis), _num(planned), _num(lower)
    if not (basis and planned and lower and bidders) or lower < MIN_LOWER_RATE:
        return None
    winner = _winner_rate(bidders, planned)
    if not winner:
        return None
    width = extra.pop("range_width", None) or range_width(prices, basis)
    if _outlier(basis, planned, width):
        return None
    rec = PastBid(notice_no=notice_no, work=work or "", basis=basis, planned=planned, lower_rate=lower,
                  winner_rate=winner, bidder_count=len(bidders), range_width=width, **extra)
    est = _estimate_floor(bidders, planned)
    if est:
        rec.floor_est, rec.floor_err = est
    net = _net_line(bidders, rec)
    if net:
        rec.net_line, rec.net_err = net
    rec.odd_remarks = Counter((b.remark or "비고 없음").strip() for b in bidders
                              if b.rank is None and b.amount and b.amount >= rec.valid_floor)
    ranked = sorted((b for b in bidders if b.rank is not None and b.amount), key=lambda b: b.rank)
    rec.top_amounts = [b.amount for b in ranked[:TOP_N]]
    rec.amounts = [b.amount for b in bidders if b.amount]
    own = next((b for b in bidders if own_bizno and _clean_bizno(b.bizno) == own_bizno), None)
    if own is not None:
        rec.own_amount, rec.own_rank = own.amount, own.rank
    return rec


def from_bid_history(item: dict, source: str = "", own_bizno: str = "") -> PastBid | None:
    """`bidprice` 결과(`BidResult.to_dict`) 1건."""
    from .bidprice import Bidder

    bidders = [Bidder(**b) for b in item.get("bidders") or []]
    prices = list(item.get("prepared_prices") or item.get("drawn_prices") or [])
    return _make(item.get("notice_no", ""), item.get("work_type", ""), item.get("basis_amount"), item.get("planned_price"),
                 item.get("lower_limit_rate"), bidders, prices,
                 range_width=_width_from_rates(item.get("range_lo"), item.get("range_hi")),
                 a_value=_num(item.get("a_value")), title=item.get("title", ""), opened_at=item.get("opened_at") or "",
                 source=source, own_bizno=own_bizno, institution=str(item.get("institution") or ""))


def parse_draws(rows: list[dict], basis: float | None) -> list[tuple[int, float, int]]:
    """예비가격 상세 행 → (번호, 기초금액 대비 %, 선택 횟수) 번호순. 같은 번호가 여러 줄이면(재입찰) 마지막 줄."""
    basis = _num(basis)
    found: dict[int, tuple[int, float, int]] = {}
    for row in rows:
        no, price, count = _num(row.get("compnoRsrvtnPrceSno")), _num(row.get("bsisPlnprc")), _num(row.get("drwtNum"))
        if basis and no and price and count is not None and 1 <= no <= 15:
            found[int(no)] = (int(no), price / basis * 100, int(count))
    return [found[k] for k in sorted(found)]


def _row_value(rows: list[dict], key: str) -> Any:
    return next((r.get(key) for r in rows if r.get(key) not in (None, "")), None)


_DATE_VALUE = re.compile(r"^\s*(?:19|20)\d{2}[-./]?\d{1,2}[-./]?\d{1,2}")


def _opened_at(rec: dict, rows: list[dict]) -> str:
    """개찰일 — 공고 정보(`notice.opened_at`) → 개찰일 칸(opengDt·rlOpengDt 등 이름에 'openg'+'dt') → 그 밖의 날짜 칸
    (inptDt·rgstDt 등 'Dt'로 끝나는 칸) 순. 응답마다 칸 이름이 달라 날짜처럼 생긴 값만 쓴다."""
    found = (rec.get("notice") or {}).get("opened_at")
    if found:
        return str(found)
    for test in (lambda k: "openg" in k.lower() and k.lower().endswith(("dt", "date")),
                 lambda k: k.lower().endswith("dt")):
        for row in rows:
            for key, value in row.items():
                if test(key) and value and _DATE_VALUE.match(str(value)):
                    return str(value)
    return ""


def skip_reason(rec: dict) -> str:
    """`from_result_record`가 못 쓴 줄의 이유(검증 화면에 건수로 보임)."""
    if rec.get("error"):
        return "개찰 결과 없음(조회 오류)"
    prices_rows, bidder_rows = rec.get("prices") or [], rec.get("bidders") or []
    planned, basis, _ = parse_prepare_prices(prices_rows)
    if not bidder_rows:
        return "참가업체 없음"
    if not (planned and basis):
        return "예정가격·기초금액 없음"
    lower = _num((rec.get("notice") or {}).get("lower_rate") or _row_value(prices_rows + bidder_rows, "sucsfbidLwltRate"))
    if not lower:
        return "하한율 없음"
    if lower < MIN_LOWER_RATE:
        return "하한율 이상값(하한선 없는 입찰)"
    info = rec.get("notice") or {}
    width = _width_from_rates(info.get("range_lo"), info.get("range_hi")) or range_width(
        [p for r in prices_rows if (p := _num(r.get("bsisPlnprc")))], basis)
    if _outlier(basis, planned, width):
        return "사정률 이상치(변동폭 밖)"
    return "1순위 없음"


def from_result_record(rec: dict, source: str = "", own_bizno: str = "") -> PastBid | None:
    """`scripts/fetch_bid_results.py` 결과 1줄(원본 행). 하한율·A값은 rec["notice"]에(10/28부터 함께 받음), 예전 줄이면 원본 행에서 찾는다."""
    if rec.get("error"):
        return None
    prices_rows, bidder_rows = rec.get("prices") or [], rec.get("bidders") or []
    planned, basis, _drawn = parse_prepare_prices(prices_rows)
    info = rec.get("notice") or {}
    lower = info.get("lower_rate") or _row_value(prices_rows + bidder_rows, "sucsfbidLwltRate")
    prices = [p for r in prices_rows if (p := _num(r.get("bsisPlnprc")))]
    return _make(rec.get("no", ""), rec.get("work", ""), basis, planned, lower, parse_bidders(bidder_rows), prices,
                 range_width=_width_from_rates(info.get("range_lo"), info.get("range_hi")),
                 a_value=_num(info.get("a_value")) if info.get("a_applies", True) else 0.0,
                 title=info.get("title", ""), opened_at=_opened_at(rec, bidder_rows + prices_rows), source=source,
                 own_bizno=own_bizno, draws=parse_draws(prices_rows, basis),
                 method=str(_row_value(prices_rows, "bidwinrSlctnAplBssCntnts") or ""),
                 institution=str(info.get("institution") or ""))


@dataclass
class History:
    records: list[PastBid] = field(default_factory=list)
    skipped: int = 0  # 하한율·예정가격·1순위가 없어 못 쓴 공고
    skip_reasons: Counter = field(default_factory=Counter)
    files: list[str] = field(default_factory=list)


def load_history(paths: Iterable[Path], own_bizno: str | None = None) -> History:
    """과거 개찰 파일들 → 공고번호당 1건(나중 파일이 앞 파일을 덮음, 정보가 더 많은 쪽을 남김).
    own_bizno(지일 사업자번호)를 주면 공고마다 지일 투찰금액·순위도 남긴다."""
    own_bizno = _clean_bizno(own_bizno)
    found: dict[str, PastBid] = {}
    history = History()
    for path in paths:
        try:
            if path.suffix == ".jsonl":
                items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
                parsed = [from_result_record(i, path.name, own_bizno) for i in items]
                history.skip_reasons.update(skip_reason(i) for i, p in zip(items, parsed) if p is None)
            else:
                items = json.loads(path.read_text(encoding="utf-8"))
                parsed = [from_bid_history(i, path.name, own_bizno) for i in items]
        except (OSError, ValueError) as err:
            log.warning("과거 개찰 파일을 읽지 못함 %s: %s", path, err)
            continue
        history.files.append(path.name)
        for rec in parsed:
            if rec is None:
                history.skipped += 1
                continue
            old = found.get(rec.notice_no)
            if old is None or (rec.a_value is not None, rec.range_width is not None) >= (old.a_value is not None, old.range_width is not None):
                found[rec.notice_no] = rec
    history.records = list(found.values())
    return history


BACKUP_MARK = "_채우기전"  # scripts/fetch_bid_results.py --fill-notice가 남기는 원본 사본 — 같은 공고가 두 번 읽히지 않게 뺀다


def history_paths(directory: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in HISTORY_PATTERNS:
        paths.extend(p for p in sorted(directory.glob(pattern)) if BACKUP_MARK not in p.stem)
    return paths


_CACHE: dict[str, Any] = {}


def default_own_bizno() -> str:
    """지일 사업자번호 — 환경변수 JIIL_BIZNO, 없으면 config/held_qualifications.json의 companyBizno."""
    import os

    env = os.environ.get("JIIL_BIZNO", "").strip()
    if env:
        return env
    try:
        path = Path(__file__).resolve().parent.parent / "config" / "held_qualifications.json"
        return str(json.loads(path.read_text(encoding="utf-8")).get("companyBizno", ""))
    except (OSError, ValueError):
        return ""


def history_from_dir(directory: Path) -> History:
    """폴더의 과거 개찰 파일 — 파일이 바뀌었을 때만 다시 읽는다(웹앱이 추천을 부를 때마다 수 MB를 읽지 않게)."""
    paths = history_paths(directory)
    key = tuple((str(p), p.stat().st_mtime_ns) for p in paths)
    if _CACHE.get("key") != key:
        _CACHE.update(key=key, history=load_history(paths, default_own_bizno()))
    return _CACHE["history"]


def _scale_ok(rec: PastBid, scale: str) -> bool:
    if scale == "small":
        return rec.bidder_count < LARGE_COMPETITION
    if scale == "large":
        return rec.bidder_count >= LARGE_COMPETITION
    return True


def similar(records: list[PastBid], work: str | None, width: int | None, scale: str = "all",
            field_name: str | None = None) -> tuple[list[PastBid], list[str]]:
    """비슷한 과거 공고 — 분야(공고명 키워드)·업무·변동폭·경쟁 규모가 같은 것. 모자라면(MIN_SAMPLES 미만) 변동폭 → 업무 → 분야
    순으로 조건을 푼다. 경쟁 규모는 사용자가 고른 것이라 풀지 않는다. 분야를 모르면('공고명 없음') 분야 조건은 안 씀."""
    pool = [r for r in records if _scale_ok(r, scale)]
    if field_name in (None, "", "공고명 없음"):
        field_name = None
    conditions = [("분야", field_name, lambda r: r.field == field_name), ("업무", work, lambda r: r.work == work),
                  ("변동폭", width, lambda r: r.range_width == width)]
    active = [(name, test) for name, value, test in conditions if value]
    while True:
        picked = [r for r in pool if all(test(r) for _, test in active)]
        if len(picked) >= MIN_SAMPLES or not active:
            break
        active.pop()  # 마지막 조건(변동폭)부터 푼다
    labels = [f"{name} {f'±{value}%' if name == '변동폭' else value}" for name, value, _ in conditions
              if value and name in {n for n, _ in active}]
    if scale != "all":
        labels.append(f"참가 {SCALES[scale]}")
    return picked, labels


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def _index(rate: float) -> int:
    return round((rate - GRID_LO) / GRID_STEP)


def _rate(index: int) -> float:
    return round(GRID_LO + index * GRID_STEP, 3)


def coverage(records: list[PastBid], ceiling=None) -> tuple[list[int], list[int]]:
    """격자 위 r마다 (1순위였을 공고 수, 유효했을 공고 수). ceiling(공고 → 상한 r)을 주면 1순위 대신 그 구간
    (예: `lambda r: r.rank_ceiling(5)` = 5위 안)을 센다."""
    ceiling = ceiling or (lambda rec: rec.win_ceiling)
    size = _index(GRID_HI) + 1
    win_d, valid_d = [0] * (size + 1), [0] * (size + 1)
    for rec in records:
        start = max(0, min(size, _index(rec.assessment) + (1 if _rate(_index(rec.assessment)) < rec.assessment else 0)))
        valid_d[start] += 1
        if not rec.window_ok:  # 하한선이 틀린 공고 — 유효는 세고 1순위·순위 구간은 안 셈
            continue
        top = min(ceiling(rec), GRID_HI + GRID_STEP)
        end = max(0, min(size, _index(top) + (1 if _rate(_index(top)) < top else 0)))
        if end > start:  # [start, end) — win_ceiling 미만까지
            win_d[start] += 1
            win_d[end] -= 1
    wins, valid, w, v = [], [], 0, 0
    for i in range(size):
        w += win_d[i]
        v += valid_d[i]
        wins.append(w)
        valid.append(v)
    return wins, valid


def _best(wins: list[int], lowest: bool = False) -> int | None:
    """1순위 수가 가장 많은 r. 같은 값이 이어진 구간이 여럿이면 가장 넓은 구간의 가운데(작은 오차에 덜 흔들리게).
    lowest=True면 가장 많은 값이 처음 나오는 r(가장 싼 금액) — 5위 안 세기는 참가 5곳 미만 공고가 위로 끝없이 '5위 안'이라
    가운데를 고르면 사정률이 지나치게 높아진다."""
    top = max(wins) if wins else 0
    if top <= 0:
        return None
    runs, start = [], None
    for i, w in enumerate(wins + [-1]):
        if w == top and start is None:
            start = i
        elif w != top and start is not None:
            runs.append((start, i - 1))
            start = None
    if lowest:
        return runs[0][0]
    lo, hi = max(runs, key=lambda run: run[1] - run[0])
    return (lo + hi) // 2


def _ratio(track: dict | None, key: str = "win") -> float:
    return track[key] / track["tried"] if track and track.get("tried") else 0.0


def trend_rate(records: list[PastBid], k: int = TOP_N) -> tuple[float, float] | None:
    """단순 경향으로 정한 사정률 = 비슷한 과거 공고의 실제 사정률 중앙 + 그 공고들 1~k위 업체가 실제 사정률보다 위에 쓴 간격의 중앙.
    (예상 사정률, 간격). 투찰은 운이 커서 검증 성적으로 고를 차이가 없을 때 쓰는 '합리적 추론'(2026-10-28 사용자 결정)."""
    if not records:
        return None
    gaps = [r.rate_for(a) - r.assessment for r in records if r.window_ok for a in r.top_amounts[:k]]
    if not gaps:
        return None
    return statistics.median(r.assessment for r in records), statistics.median(gaps)


def _competitor_rates(rec: PastBid):
    """이 공고 참가업체 금액 → 가정 사정률 배열(공고마다 한 번만 계산해 둠)."""
    import numpy as np

    cached = getattr(rec, "_crowd_rates", None)
    if cached is None:
        amounts = np.asarray(rec.amounts, dtype=float)
        cached = (rec.planned + (amounts - rec.floor) / (rec.lower_rate / 100)) / rec.basis * 100
        if rec.net_line:  # `rate_for`와 같게 — 순공사원가 선 기준 값과 작은 쪽
            cached = np.minimum(cached, amounts / rec.net_line * rec.assessment)
        rec._crowd_rates = cached
    return cached


def _smooth(values, weights, edges, bandwidth: float):
    """가중 히스토그램을 가우스 곡선으로 매끄럽게 — 넓이 1인 밀도(격자 칸마다)."""
    import numpy as np

    step = edges[1] - edges[0]
    idx = np.floor((values - edges[0]) / step).astype(np.int64)
    keep = (idx >= 0) & (idx < len(edges) - 1)
    hist = np.bincount(idx[keep], weights=weights[keep], minlength=len(edges) - 1)  # 고른 칸이라 np.histogram(정렬)보다 빠름
    half = max(1, int(round(4 * bandwidth / step)))
    x = np.arange(-half, half + 1) * step
    kernel = np.exp(-0.5 * (x / bandwidth) ** 2)
    # 곡선이 칸 수보다 길면 mode="same"이 곡선 길이로 돌려줘 격자와 어긋난다(10/29 서버 오류 — 공고가 적고 사정률이 넓게 퍼질 때)
    dens = np.convolve(hist, kernel / kernel.sum(), mode="full")[half: half + len(hist)]
    total = dens.sum() * step
    return dens / total if total > 0 else dens


def crowd_rate(records: list[PastBid], today: str | None = None, half_life: float | None = CROWD_HALF_LIFE) -> dict | None:
    """대규모 경쟁용 추천 — "이 사정률 r로 썼으면 1순위였을 확률"이 가장 높은 r.

    1순위 ⇔ 실제 사정률 s ≤ r 이고 [s, r) 사이에 다른 업체가 없음. 과거 비슷한 공고에서
      f(s) = 실제 사정률 분포(최근 공고에 무게 — 반감기 half_life일, None이면 무게 없이 같게),
      g(c) = 경쟁업체 가정 사정률 분포(공고마다 무게 같게), N = 참가 업체 수 중앙
    를 구하고 P(r) = ∫_{s≤r} f(s)·exp(−N·∫_s^r g) ds 를 격자 위에서 계산한다(업체들이 g를 따라 서로 따로 낸다고 봄).
    결과 {rate, win_p(모형 1순위 확률), valid_p(유효 확률 = s ≤ r), bidders, samples}. 자료가 MIN_SAMPLES건 미만이면 None."""
    import numpy as np
    from .bidbacktest import date_of

    recs = [r for r in records if r.amounts]
    if len(recs) < MIN_SAMPLES:
        return None
    dated = sorted(((date_of(r) or "", r) for r in recs), key=lambda x: x[0])[-CROWD_MAX:]
    recs = [r for _, r in dated]
    dates = [d or None for d, _ in dated]
    s = np.array([r.assessment for r in recs])
    ref = today or max((d for d in dates if d), default=None)

    def age(d: str | None) -> float:
        if not (ref and d):
            return (half_life or CROWD_HALF_LIFE) * 2.0  # 날짜 모르면 오래된 공고로 침
        from datetime import date

        return max(0, (date.fromisoformat(ref) - date.fromisoformat(d)).days)

    w = np.array([0.5 ** (age(d) / half_life) if half_life else 1.0 for d in dates])
    lo, hi = max(GRID_LO, float(s.min()) - 0.5), min(GRID_HI, float(s.max()) + 0.5)
    edges = np.arange(lo, hi + CROWD_STEP, CROWD_STEP)
    grid = (edges[:-1] + edges[1:]) / 2
    q75, q25 = np.percentile(s, [75, 25])
    spread = min(float(s.std()), float(q75 - q25) / 1.349) or float(s.std()) or 0.1
    bw = max(0.03, 0.9 * spread * len(s) ** -0.2)
    f = _smooth(s, w, edges, bw)
    trusted = [(r, wi) for r, wi in zip(recs, w) if r.window_ok] or list(zip(recs, w))
    comp = [_competitor_rates(r) for r, _ in trusted]
    cw = np.concatenate([np.full(len(c), wi / len(c)) for c, (_, wi) in zip(comp, trusted)])
    g = _smooth(np.concatenate(comp), cw, edges, CROWD_BW_COMPETITOR)
    n = float(statistics.median(r.bidder_count for r in recs))
    decay = np.exp(-n * g * CROWD_STEP)
    win = np.empty_like(f)
    p = 0.0
    for k in range(len(f)):
        p = p * decay[k] + f[k] * CROWD_STEP
        win[k] = p
    valid = np.cumsum(f) * CROWD_STEP
    # 하한선 통과 확률이 PICK_MIN_VALID 이상인 r 중에서만 고름(10/29 R 공고 63건: 제약 없이는 경쟁업체가 없는 낮은 쪽 끝을 골라
    # 실제보다 중앙 −1.09%p, ±3% 공고 29건 중 통과 3건 — 1순위 확률은 그 끝에서 아주 조금 높을 뿐이었음)
    allowed = valid >= PICK_MIN_VALID
    k = int(np.where(allowed, win, -1.0).argmax()) if allowed.any() else int(win.argmax())
    return {"rate": round(float(grid[k]), 3), "win_p": float(win[k]), "valid_p": float(min(1.0, valid[k])),
            "bidders": n, "samples": len(recs)}


def _rank_of(o: dict) -> float:
    """평균 순위(참가업체 중 상위 몇 %, 미달은 꼴찌 — 작을수록 좋음). 모르면 맨 뒤."""
    r = (o.get("track") or {}).get("rank")
    return r if r is not None else 2.0


def pick_option(options: list[dict], outsourced: dict | None = None) -> dict | None:
    """화면 맨 위 '추천 투찰금액'(= 투찰 계산기 '투찰금액')으로 쓸 방식 하나 — 경쟁 규모와 관계없이 같은 규칙(10/29 사용자 결정 2안).

    **조건: 검증(개찰 전 자료로 정했다면) 하한선 통과 비율 PICK_MIN_VALID 이상**(하한율 만족은 조건, 금액은 경향으로).
    검증 MIN_PICK_TRIED건 미만 방식은 안 봄. 조건을 만족하는 방식 중에서:
      - 1순위 비율 최고가 PICK_MIN_WIN 이상(1순위로 방식이 가려짐 — 주로 소규모) → 1순위 → 5위 안 → 통과 비율 순(mode="track")
      - 아니면(1순위가 거의 0 — 주로 대규모) → **평균 순위(상위 몇 %, 하한선 미달은 꼴찌로 셈)가 가장 좋은 방식**(mode="rank")
        (10/29 R 공고 63건: 대규모 공사는 모든 방식이 1순위 0~2건이라 1순위 비율로는 통과율 높은(비싼) 방식만 뽑혔음)
    조건을 만족하는 방식이 하나도 없으면 하한선 통과 비율이 가장 높은 방식(mode="valid"), 검증이 전혀 없으면 고정값(mode="fixed")."""
    if not options:
        return None
    by_win = lambda o: (_ratio(o["track"]), _ratio(o["track"], "top"), _ratio(o["track"], "valid"))  # noqa: E731
    by_rank = lambda o: (-_rank_of(o), _ratio(o["track"], "valid"))  # noqa: E731 — 큰 값이 좋게(정렬·max 같이 씀)
    tried = lambda o: (o.get("track") or {}).get("tried", 0) >= MIN_PICK_TRIED  # noqa: E731
    passes = lambda o: tried(o) and _ratio(o["track"], "valid") >= PICK_MIN_VALID  # noqa: E731
    every = [o for o in options if passes(o)]
    measured = [o for o in options if tried(o)]
    key = by_win
    if every:
        best = max(every, key=by_win)  # 같은 성적이면 표 위쪽 방식
        if _ratio(best["track"]) >= PICK_MIN_WIN:
            chosen, mode = best, "track"
        else:
            key = by_rank
            chosen, mode = max(every, key=by_rank), "rank"
    elif measured:
        chosen, mode = max(measured, key=lambda o: (_ratio(o["track"], "valid"), _ratio(o["track"]))), "valid"
    else:
        chosen = next((o for o in options if o["name"].startswith("고정")), options[0])
        mode = "fixed"
    # 우선순위 목록 — 화면 계산기가 이 순서대로 보며 '모의 예정가격 하한선'을 넘는 첫 방식을 투찰금액으로 씀:
    # 고른 방식 → 조건(통과 PICK_MIN_VALID 이상)을 만족하는 나머지(같은 기준 순) → 조건 미달(통과 비율순)
    rest = sorted((o for o in every if o is not chosen), key=key, reverse=True)
    others = [o for o in measured if o not in every and o is not chosen]
    others.sort(key=lambda o: (_ratio(o["track"], "valid"), _ratio(o["track"])), reverse=True)
    order = [o["name"] for o in [chosen, *rest, *others]]
    own = (outsourced or {}).get("track")
    return {**chosen, "weak": not every, "mode": mode, "own_track": own, "min_valid": PICK_MIN_VALID,
            "min_win": PICK_MIN_WIN, "order": order,
            "beats_outsourced": bool(own and own.get("tried") and _ratio(chosen.get("track")) > _ratio(own))}


def _model_options(history: History, picked: list[PastBid], scale: str, row, target: dict) -> list[dict]:
    """논문 차용 세 방식(`bidmodels` — 추첨 모형·가중 KNN·간격 예측)의 표 줄. 자료가 모자라면 그 줄은 빠짐."""
    from . import bidmodels

    out = []
    got = bidmodels.draw_rate(picked)
    if got:
        out.append({**row(bidmodels.DRAW_NAME, got["rate"]), "win_p": got["win_p"], "valid_p": got["valid_p"]})
    pool = [r for r in history.records if _scale_ok(r, scale)]
    got = bidmodels.knn_rate(pool, target)
    if got:
        out.append({**row(bidmodels.KNN_NAME, got["rate"]), "win_p": got["win_p"], "valid_p": got["valid_p"]})
    first = history.records[0] if history.records else None
    model = bidmodels.gap_model(history.records, key=("now", len(history.records), id(first)))
    got = bidmodels.gap_rate(picked, model, target)
    if got:
        out.append({**row(bidmodels.GAP_NAME, got["rate"]), "win_p": got["win_p"], "valid_p": got["valid_p"],
                    "gap": got["gap"]})
    return out


_TRACK_CACHE: dict[tuple, dict] = {}


def _track(history: History, picked: list[PastBid], scale: str) -> dict[str, dict]:
    """비슷한 공고마다 '그 공고 개찰일 전 자료로 이 방식대로 정했다면' 1순위·유효였나(`bidbacktest`와 같은 채점).
    in-sample 건수(위 표의 wins)는 고른 자료로 다시 맞춘 것이라 후하게 나와서, 화면엔 이 검증 성적을 함께 보인다."""
    from .bidbacktest import backtest, date_of, track_records

    dated = sorted((r for r in picked if date_of(r)), key=lambda r: date_of(r) or "")[-TRACK_LIMIT:]
    targets = {r.notice_no for r in dated}
    key = (id(history), len(history.records), scale, frozenset(targets))
    if key not in _TRACK_CACHE:
        if len(_TRACK_CACHE) > 64:
            _TRACK_CACHE.clear()
        rows, _ = backtest(history.records, scale=scale, fixed=(FIXED_RATE,), targets=targets)
        _TRACK_CACHE[key] = track_records(rows)
    return _TRACK_CACHE[key]


def bid_amount(basis: float, rate: float, lower_rate: float, a: float = 0.0, net_cost: float | None = None,
               net_rate: float | None = None) -> float:
    """투찰금액 = A + (기초금액 × 사정률 − A) × 하한율 (끝자리 처리 전 — 화면이 기타설정대로 자른다).
    공고문에 순공사원가 조건(net_rate %, `bidcalc.net_cost_rule`)이 있으면 순공사원가 × 사정률 × net_rate와 높은 쪽."""
    planned = basis * rate / 100
    amount = a + (planned - a) * lower_rate / 100
    if net_cost and net_rate:
        amount = max(amount, net_cost * rate / 100 * net_rate / 100)
    return amount


def recommend(history: History, work: str | None = None, lo: float | None = None, hi: float | None = None,
              scale: str = "all", basis: float | None = None, lower_rate: float | None = None, a: float = 0.0,
              title: str = "", institution: str = "", method: str = "", net_cost: float | None = None,
              net_rate: float | None = None) -> dict:
    """화면 '추천 사정률' 칸 자료. 금액(basis·lower_rate)을 주면 추천 사정률로 계산한 투찰금액도 넣는다.
    title(공고명)을 주면 같은 분야(공고명 키워드, `nego.bidfield`) 과거 공고를 먼저 고르고, 그 묶음의 1~5위·지일 경향을 함께 싣는다.
    scale="auto"(화면 기본)면 비슷한 과거 공고의 참가 업체 수 중앙값으로 경쟁 규모를 고른다(투찰 전엔 참가 수를 모르므로)."""
    width = _width_from_rates(lo, hi)
    field_name = classify(title) if title else None
    auto_note = ""
    if scale == "auto":
        base, _ = similar(history.records, work, width, "all", field_name)
        if base:
            median = statistics.median(r.bidder_count for r in base)
            scale = "small" if median < LARGE_COMPETITION else "large"
            auto_note = f"경쟁 규모 자동: 비슷한 과거 공고 참가 중앙 {median:g}곳 → {SCALES[scale]}"
        else:
            scale = "all"
    scale = scale if scale in SCALES else "all"
    picked, labels = similar(history.records, work, width, scale, field_name)
    out: dict[str, Any] = {"field": field_name,
        "samples": len(picked), "total": len(history.records), "skipped": history.skipped,
        "files": history.files, "conditions": labels, "scale": SCALES[scale], "notes": [],
    }
    if auto_note:
        out["auto_scale"] = auto_note
    if not history.records:
        out["notes"].append("과거 개찰 자료가 없습니다 — 서버 PC에서 `python -m nego.bidprice` 또는 "
                            "`scripts/fetch_bid_results.py`로 output/에 받아 두세요")
        return out
    if len(picked) < MIN_SAMPLES:
        out["notes"].append(f"비슷한 과거 공고가 {len(picked)}건뿐이라 추천 근거가 약해요 — 참고만 하세요")
    if not picked:
        return out

    rates = [r.assessment for r in picked]
    gaps = [r.winner_rate - r.lower_rate for r in picked]
    counts = [r.bidder_count for r in picked]
    out["assessment"] = {"p10": _pct(rates, 0.1), "p50": statistics.median(rates), "p90": _pct(rates, 0.9),
                         "min": min(rates), "max": max(rates)}
    out["winner_gap"] = statistics.median(gaps)
    out["bidders"] = {"median": statistics.median(counts), "max": max(counts),
                      "large": sum(c >= LARGE_COMPETITION for c in counts)}
    no_a = sum(r.floor_source == "A=0" for r in picked)
    if no_a:
        out["notes"].append(f"비슷한 과거 공고 중 {no_a}건은 하한선을 정확히 알 수 없어(A값 정보·미달 업체 없음) A값 0으로 계산했어요 — "
                            "이 공고들은 1순위 성적이 실제보다 좋게 나올 수 있어요")
    if out["bidders"]["large"] > len(picked) / 2:
        out["notes"].append(f"참가 {LARGE_COMPETITION}곳 이상 대규모 경쟁이 많습니다 — 1순위가 하한선 바로 위라 "
                            "어떤 사정률도 몇 건밖에 못 이깁니다(운의 영역)")

    wins, valid = coverage(picked)
    n = len(picked)

    def row(name: str, rate: float) -> dict:
        i = max(0, min(len(wins) - 1, _index(rate)))
        item = {"name": name, "rate": round(rate, 3), "wins": wins[i], "valid": valid[i],
                "win_ratio": wins[i] / n, "valid_ratio": valid[i] / n}
        if basis and lower_rate:
            item["bid"] = bid_amount(basis, rate, lower_rate, a, net_cost, net_rate)
        return item

    best = _best(wins)
    options = []
    if best is not None:
        options.append(row("추천(과거 1순위 최다)", _rate(best)))
    options += [row("사정률 중앙값", out["assessment"]["p50"]), row("유효 70%", _pct(rates, 0.7)),
                row("유효 90%", out["assessment"]["p90"])]
    top_best = _best(coverage(picked, lambda r: r.rank_ceiling(TOP_N))[0], lowest=True)
    if top_best is not None:
        options.insert(1 if best is not None else 0, row(f"추천(과거 {TOP_N}위 안 최다)", _rate(top_best)))
    options.append(row(f"고정 {FIXED_RATE:g}%", FIXED_RATE))
    guess = trend_rate(picked)
    if guess is not None:
        options.append({**row(TREND_NAME, guess[0] + guess[1]), "base": round(guess[0], 3), "gap": round(guess[1], 3)})
    if out["bidders"]["large"] > len(picked) / 2:
        crowd = crowd_rate(picked)
        if crowd is not None:
            options.append({**row(CROWD_NAME, crowd["rate"]), "win_p": crowd["win_p"], "valid_p": crowd["valid_p"],
                            "crowd_bidders": crowd["bidders"]})
    options += _model_options(history, picked, scale, row, {
        "field": field_name if field_name not in (None, "", "공고명 없음") else None, "work": work, "width": width,
        "lower_rate": lower_rate, "method": method, "basis": basis,
        "region": region_of(institution)})
    track = _track(history, picked, scale)
    for o in options:
        o["track"] = track.get(o["name"])
    own = [r for r in picked if r.own_amount]
    if own:
        rates_own = [r.rate_for(r.own_amount) for r in own]
        out["outsourced"] = {"name": OWN_NAME, "rate": round(statistics.median(rates_own), 3), "track": track.get(OWN_NAME)}
    from .bidtrend import trend

    out["trend"] = trend(picked)
    out["options"] = options
    out["best"] = options[0] if best is not None else None
    out["pick"] = pick_option(options, out.get("outsourced"))
    if best is None:
        out["notes"].append("과거 공고에서 1순위가 될 수 있었던 사정률이 없습니다(자료 부족)")
    # 그래프·표용 거친 곡선(0.05%p 간격, 자료가 있는 범위만)
    lo_i, hi_i = _index(min(rates) - 0.3), _index(max(rates) + 0.3)
    step = round(0.05 / GRID_STEP)
    out["curve"] = [{"rate": _rate(i), "wins": wins[i], "valid": valid[i]}
                    for i in range(max(0, lo_i), min(len(wins), hi_i + 1), step)]
    return out
