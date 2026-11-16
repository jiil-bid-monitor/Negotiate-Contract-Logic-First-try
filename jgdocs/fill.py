"""나라장터 공고·개찰 결과 → 적격심사 기본사항 칸 값(2026-11-16 사용자 요청 — 공고번호로 기본사항 채우기).

입력: 투찰내역 기록 한 줄(webapp.bidlog — 공고번호·차수·지일 투찰금액, 개찰 결과 result.plannedPrice·own.amount·aValue)과
nego.bidcalc.lookup 결과(공고명·수요기관·추정가격·개찰일시·입찰공고일·세부품명·A값·업종). 못 찾은 칸은 넣지 않음(담당자가 채움).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

GOODS, WORKS = "물품", "공사"
BILLION = 1_000_000_000


def choose(work: str | None, est: float | None) -> tuple[str, str] | None:
    """(업무, 파일 묶음) — 물품·공사만(용역은 일반용역 기준을 정하기 전이라 None). 추정가격을 모르면 10억 미만."""
    if work not in (GOODS, WORKS):
        return None
    return work, "10억이상" if (est or 0) >= BILLION else "10억미만"


def _day(text: str | None) -> dt.datetime | None:
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        try:
            return dt.datetime.strptime(str(text or "").strip()[:n], fmt)
        except ValueError:
            continue
    return None


def _minute(text: str | None) -> str | None:
    d = _day(text)
    return d.strftime("%Y-%m-%d %H:%M") if d else None


def values(work: str, entry: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """기본사항 칸 이름 → 값. 이름은 jgdocs.goods.FIELDS / jgdocs.works.FIELDS의 이름 그대로."""
    res = entry.get("result") or {}
    own = res.get("own") or {}
    no = str(entry.get("noticeNo") or info.get("notice_no") or "").strip()
    ordn = str(entry.get("noticeOrd") or info.get("notice_ord") or "").strip()
    out: dict[str, Any] = {
        "입찰일시": _minute(info.get("opening_at") or entry.get("openingAt")),
        "입찰공고일": _day(info.get("notice_date")),
        "수요기관": info.get("demand_institution") or info.get("institution") or entry.get("institution"),
        "입찰금액": own.get("amount") or entry.get("bid"),  # 개찰 결과의 지일 실제 투찰금액, 없으면 기록한 금액
        "추정가격": info.get("est_price"),
        "예정가격": res.get("plannedPrice") or entry.get("planned"),
    }
    number = f"{no}-{ordn}" if no and ordn else no
    title = info.get("title") or entry.get("title")
    if work == GOODS:
        out.update({"입찰등록번호": number, "입찰 대상 물품명": info.get("product_name") or title,
                    "정부물품 분류번호": info.get("product_no")})
    else:
        lic = [x for x in (info.get("licenses") or []) if x]
        a = res.get("aValue")
        out.update({"입찰공고번호": number, "공사명": title, "A값": a if a is not None else info.get("a_value"),
                    "업종": lic[0] if len(lic) == 1 else None})
        if len(lic) == 1:
            out["업종평가비율(%)"] = 100  # 업종이 하나뿐인 공고는 평가비율 100%(원문 — 복합 업종일 때만 공고가 비율을 정함)
    return {k: v for k, v in out.items() if v not in (None, "")}


def apply(xlsx, fields: list[tuple], vals: dict[str, Any], sheet: str = "기본사항") -> list[str]:
    """만든 표준틀의 기본사항 칸에 값을 넣음. 넣은 칸 이름 목록."""
    from openpyxl import load_workbook

    wb = load_workbook(xlsx)
    ws = wb[sheet]
    done = []
    for row, name, *_ in fields:
        if name in vals:
            ws.cell(row, 2, vals[name])
            done.append(name)
    wb.save(xlsx)
    return done


if __name__ == "__main__":  # 자체 확인
    e = {"noticeNo": "R26BK00000001", "noticeOrd": "000", "bid": 100, "result": {"plannedPrice": 120, "own": {"amount": 101}, "aValue": 5}}
    i = {"title": "시험 공사", "est_price": 3e8, "opening_at": "2026-11-20 11:00:00", "notice_date": "2026-11-01 09:00:00",
         "licenses": ["실내건축공사업"]}
    v = values(WORKS, e, i)
    assert v["입찰공고번호"] == "R26BK00000001-000" and v["입찰금액"] == 101 and v["A값"] == 5 and v["업종평가비율(%)"] == 100, v
    assert v["입찰공고일"] == dt.datetime(2026, 11, 1, 9, 0) and v["입찰일시"] == "2026-11-20 11:00"
    assert choose("물품", 1.2e9) == ("물품", "10억이상") and choose("용역", 1e8) is None
    print("ok")
