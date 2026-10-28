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
TRACK_LIMIT = 400  # 검증 성적은 비슷한 공고 중 최근 이만큼만 채점(화면 응답 시간 — 공고마다 그 전 공고로 추천을 다시 만듦)


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
    top_amounts: list[float] = field(default_factory=list)  # 개찰 1~5위 투찰금액(순위순) — 투찰 경향(`nego.bidtrend`)

    @property
    def field(self) -> str:
        """공고명 키워드로 나눈 분야(`nego.bidfield`)."""
        return classify(self.title)

    @property
    def assessment(self) -> float:
        return self.planned / self.basis * 100

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

    def rate_for(self, amount: float) -> float:
        """이 공고에 이 금액을 냈을 때의 가정 사정률. 금액 − 하한선 = 하한율 × (기초 × r − 예정가격)이라 A값이 따로 필요 없다."""
        return (self.planned + (amount - self.floor) / (self.lower_rate / 100)) / self.basis * 100

    @property
    def win_ceiling(self) -> float:
        """이 r 미만이면 1순위(이상이면 1순위보다 비쌈)."""
        return self.rate_for(self.winner_rate / 100 * self.planned)

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


def _estimate_floor(bidders: list, planned: float) -> float | None:
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
    return (max(b.amount for b in below) + low_ok) / 2


def _clean_bizno(value: str | None) -> str:
    return (value or "").replace("-", "").strip()


def _make(notice_no: str, work: str, basis, planned, lower, bidders: list, prices: list[float], own_bizno: str = "",
          **extra) -> PastBid | None:
    basis, planned, lower = _num(basis), _num(planned), _num(lower)
    if not (basis and planned and lower and bidders):
        return None
    winner = _winner_rate(bidders, planned)
    if not winner:
        return None
    width = extra.pop("range_width", None) or range_width(prices, basis)
    rec = PastBid(notice_no=notice_no, work=work or "", basis=basis, planned=planned, lower_rate=lower,
                  winner_rate=winner, bidder_count=len(bidders), range_width=width, **extra)
    rec.floor_est = _estimate_floor(bidders, planned)
    ranked = sorted((b for b in bidders if b.rank is not None and b.amount), key=lambda b: b.rank)
    rec.top_amounts = [b.amount for b in ranked[:TOP_N]]
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
                 source=source, own_bizno=own_bizno)


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
    if not ((rec.get("notice") or {}).get("lower_rate") or _row_value(prices_rows + bidder_rows, "sucsfbidLwltRate")):
        return "하한율 없음"
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
                 own_bizno=own_bizno)


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


def pick_option(options: list[dict], outsourced: dict | None = None) -> dict | None:
    """화면 맨 위 '추천 투찰금액'으로 쓸 방식 하나 — 검증(개찰 전 자료로 정했다면) 1순위 비율이 가장 높은 방식,
    같으면 5위 안 비율, 그다음 유효 비율이 높은 쪽. 검증 건수가 MIN_PICK_TRIED 미만인 방식만 있으면 고정값으로 두고 '근거 약함'."""
    if not options:
        return None
    ok = [o for o in options if (o.get("track") or {}).get("tried", 0) >= MIN_PICK_TRIED]
    weak = not ok
    if ok:
        chosen = max(ok, key=lambda o: (_ratio(o["track"]), _ratio(o["track"], "top"), _ratio(o["track"], "valid")))
    else:
        chosen = next((o for o in options if o["name"].startswith("고정")), options[0])
    own = (outsourced or {}).get("track")
    pick = {**chosen, "weak": weak, "own_track": own,
            "beats_outsourced": bool(own and own.get("tried") and _ratio(chosen.get("track")) > _ratio(own))}
    return pick


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


def bid_amount(basis: float, rate: float, lower_rate: float, a: float = 0.0) -> float:
    """투찰금액 = A + (기초금액 × 사정률 − A) × 하한율 (끝자리 처리 전 — 화면이 기타설정대로 자른다)."""
    planned = basis * rate / 100
    return a + (planned - a) * lower_rate / 100


def recommend(history: History, work: str | None = None, lo: float | None = None, hi: float | None = None,
              scale: str = "all", basis: float | None = None, lower_rate: float | None = None, a: float = 0.0,
              title: str = "") -> dict:
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
        out["notes"].append(f"비슷한 과거 공고가 {len(picked)}건뿐이라 추천을 믿기 어렵습니다 — 참고만 하세요")
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
        out["notes"].append(f"하한선을 정하지 못한 과거 공고 {no_a}건(A값·하한 미달 업체 없음)은 A=0으로 계산 — "
                            "1순위 구간이 실제보다 넓게 잡힐 수 있음")
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
            item["bid"] = bid_amount(basis, rate, lower_rate, a)
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
