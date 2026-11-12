"""협상에 의한 계약 — 입찰가격 평가(가격 점수) 식 읽기·계산 (2026-11-12 사용자 요청: 협상 공고 가격 점수표).

제안요청서 '입찰가격 평가' 절의 평정산식을 읽는다. 지방계약(행정안전부 예규 '지방자치단체 입찰시 낙찰자 결정기준'
제7장 협상에 의한 계약)의 모양을 기준으로 함 — 당진교육종합센터 R26BK01802876 제안요청서:

  ① 입찰가격이 예정가격의 100분의 80 이상: 평점 = 배점 × (최저입찰가격 / 해당입찰가격)
  ② 100분의 80 미만: 평점 = 배점 × (최저입찰가격 / 예정가격의 80%) + 2 × (예정가격의 80% − 해당입찰가격) / (예정가격의 80% − 70%)
  ※ 최저입찰가격이 예정가격의 100분의 70 미만이면 100분의 70으로 계산, 해당입찰가격이 100분의 70 미만이면 배점한도의 30%.

숫자(80·70·2·30%·배점)는 공고마다 제안요청서에서 읽고, 못 읽은 것은 위 값으로 둠(`found`에 읽은 항목).
예정가격을 작성하지 않으면 추정가격 + 부가가치세가 기준 — 화면에선 기초금액(없으면 추정가격 × 1.1)을 기준 금액으로 쓰고 고칠 수 있게 함.
"""
from __future__ import annotations

import re

DEFAULT = {"weight": 10.0, "upper": 80.0, "lower": 70.0, "bonus": 2.0, "floor": 70.0, "under": 30.0}

_SP = r"[\s ]*"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_rule(text: str) -> dict | None:
    """제안요청서 글 → 가격 점수 식의 숫자들. 최저입찰가격 / 해당입찰가격 식이 없으면 None."""
    flat = re.sub(r"\s+", " ", text or "")
    m = re.search(r"최저\s*입찰\s*가격\s*/\s*해당\s*입찰\s*가격", flat)
    if not m:
        return None
    # 식 앞뒤만 봄 — 문서 다른 곳의 '100분의 80'(하도급 등)을 잡지 않게
    start = max(0, m.start() - 600)
    chunk = flat[start:m.start() + 1500]
    rule = dict(DEFAULT)
    found: list[str] = []
    w = re.search(r"(?:입찰\s*)?가격\s*평가\s*[\(（]\s*(\d+(?:\.\d+)?)\s*점", flat) or \
        re.search(r"입찰\s*가격\s*평가\s*[\(（]?\s*(\d+(?:\.\d+)?)\s*[\)）]", flat)
    if w:
        rule["weight"], _ = _num(w.group(1)), found.append("weight")
    u = re.search(r"100\s*분의\s*(\d+)\s*이상인\s*경우", chunk)
    if u:
        rule["upper"], _ = _num(u.group(1)), found.append("upper")
    pcts = [_num(x) for x in re.findall(r"(\d+)\s*%\s*상당\s*가격", chunk)]
    low = next((p for p in pcts if p < rule["upper"]), None)
    if low is not None:
        rule["lower"], _ = low, found.append("lower")
    b = re.search(r"[\[［]\s*(\d+(?:\.\d+)?)\s*[×xX*]\s*[\(（]", chunk)
    if b:
        rule["bonus"], _ = _num(b.group(1)), found.append("bonus")
    f = re.search(r"최저\s*입찰\s*가격[^.。]{0,80}?100\s*분의\s*(\d+)\s*미만일?\s*경우에는\s*100\s*분의\s*(\d+)\s*으로", chunk)
    if f:
        rule["floor"], _ = _num(f.group(2)), found.append("floor")
    p = re.search(r"배점\s*한도의\s*(\d+)\s*%", chunk)
    if p:
        rule["under"], _ = _num(p.group(1)), found.append("under")
    base = "예정가격"
    if not re.search(r"예정\s*가격", chunk) and re.search(r"추정\s*가격", chunk):
        base = "추정가격"
    rule.update({"base": base, "found": found,
                 "kind": "지방" if re.search(r"지방자치단체|행정안전부", chunk) else ("국가" if re.search(r"기획재정부|국가를\s*당사자", chunk) else ""),
                 "quote": flat[max(0, m.start() - 250):m.start() + 450].strip()})
    return rule


def score(price: float, lowest: float, base: float, rule: dict) -> float | None:
    """해당입찰가격 price의 가격 점수. lowest = 유효한 입찰자 중 최저입찰가격(내 가격 포함), base = 예정가격(기준 금액)."""
    if not (price > 0 and lowest > 0 and base > 0):
        return None
    w = rule["weight"]
    upper, lower = base * rule["upper"] / 100, base * rule["lower"] / 100
    low = max(min(lowest, price), base * rule["floor"] / 100)
    if price < lower:
        return round(w * rule["under"] / 100, 4)
    if price >= upper:
        return round(w * low / price, 4)
    return round(w * low / upper + rule["bonus"] * (upper - price) / (upper - lower), 4)


def table(base: float, rule: dict, mine: list[float], rivals: list[float]) -> list[dict]:
    """내 제안가격(기준 금액 대비 %) × 다른 업체 최저가(%) → 점수표 줄."""
    out = []
    for m in mine:
        price = base * m / 100
        out.append({"pct": m, "price": price,
                    "scores": [score(price, base * r / 100, base, rule) for r in rivals]})
    return out
