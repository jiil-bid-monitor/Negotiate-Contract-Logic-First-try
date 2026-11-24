"""전력그룹사 전자입찰(SRM) 공고를 전력데이터 개방포털 API로 받아 보고, 지금 수집 규칙에 걸리는 건수를 세는 확인 도구.
2026-11-24 사용자 요청 — SRM(srm.kepco.net) 공고는 나라장터 API에 없어 웹앱이 못 받음. 웹앱에 붙일 만한지 먼저 본다(수집에는 아직 안 붙임).

서버 PC에서(전력데이터 개방포털 인증키가 있는 곳):

    py scripts/power_bid_check.py                 # 최근 30일 공고, 한전 + 발전 계열사 전부
    py scripts/power_bid_check.py --days 90       # 기간 늘리기(90일씩 나눠 받음)
    py scripts/power_bid_check.py --company COM01 --company COM08   # 한국전력공사·한국동서발전만
    py scripts/power_bid_check.py --input 받은응답.json             # 저장해 둔 응답으로(인터넷 없이)

- API: `https://bigdata.kepco.co.kr/openapi/v1/electContract.do` — 공공데이터포털(나라장터 키)이 아니라 **전력데이터 개방포털 전용 키**.
  인증키는 환경변수 `KEPCO_API_KEY`, 없으면 레포 루트 `.env`의 같은 이름. 파라미터 apiKey·noticeBeginDate·noticeEndDate(YYYYMMDD, 최대 90일)·returnType=json,
  companyId를 빼면 전 계열사. 응답은 `{"data": [...]}`(결과 코드·쪽 나눔 없음 — 기간이 곧 응답 크기).
  사양은 공개 저장소 aohus/bidding의 정리(2026-10 실측)를 참고 — 이 환경은 그 주소에 접속이 막혀 실제 호출로는 확인 못 함.
- 공고마다 지금 수집 규칙(`config/keywords.json` — 관심·제외 키워드, '통신'은 공사만 등)을 공고명에 그대로 적용(`nego.screen.screen`).
  보유 면허 경로·세부품명·자격 판정은 안 함(이 API엔 면허 코드·세부품명이 없음 — 참가자격은 글로만 옴).
- 출력: 화면 요약 + `output/전력그룹사_공고점검_날짜.csv`(공고마다 한 줄 — 회사·업무·계약/낙찰방법·상태·마감·금액·공고명·판정·참가자격 앞부분).
  담당자 이름·연락처 칸은 안 남김. `--save-raw`면 받은 응답 그대로 `output/`에(담당자 연락처가 들어 있어 **공개 저장소에 올리지 말 것**).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

API_URL = "https://bigdata.kepco.co.kr/openapi/v1/electContract.do"
KEY_NAME = "KEPCO_API_KEY"
MAX_WINDOW_DAYS = 90  # 공고 시작일~종료일 간격 상한(사양)

COMPANY_NAMES = {
    "COM01": "한국전력공사", "COM02": "한국서부발전", "COM03": "한국전력국제원자력대학원대학교",
    "COM04": "한국남부발전", "COM05": "한국중부발전", "COM06": "한국남동발전", "COM08": "한국동서발전",
    "COM09": "한국전력기술", "COM10": "한전KPS", "COM11": "한국전력거래소", "COM12": "한국원자력연료",
    "COM14": "한국발전교육원", "COM16": "한국해상풍력", "COM19": "KAPES",
}
# 도급구분 → 웹앱 업무 이름('통신'은 공사만 같은 업무 한정 규칙이 이 값을 봄)
WORK_TYPES = {"Construction": "공사", "Service": "용역", "Product": "물품"}
PURCHASE_TYPES = {"Product": "물품", "ConstructionService": "공사·용역"}
COMPETITION = {"Open": "일반경쟁", "Destination": "지명경쟁", "Limited": "제한경쟁", "Private": "수의계약"}
BID_TYPES = {"LimitedLowestPrice": "제한적최저가", "LowestPrice": "최저가", "QualifiedEval": "적격심사",
             "CollectivelyBid": "일괄입찰", "Nego": "협상", "TotalEvalSuccess": "종합심사낙찰제"}
STATES = {"PreAttendProgress": "공고진행", "AttendProgress": "입찰진행", "Close": "마감", "Fail": "유찰",
          "OpenTimed": "개찰", "Final": "공고종료"}
# 받은 칸이 얼마나 채워져 오는지 볼 칸(웹앱에 붙일 때 쓸 만한지)
CHECK_FIELDS = ("no", "name", "placeName", "itemType", "competitionType", "bidType", "noticeDate", "endDatetime",
                "bidAttendReqCloseDatetime", "presumedPrice", "presumedAmount", "bidAttendRestrict", "etc",
                "jointSupplyDemandYn", "filenlink1")


class FetchError(Exception):
    pass


def api_key() -> str:
    key = os.environ.get(KEY_NAME, "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{KEY_NAME}="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    return urllib.parse.unquote(key) if "%" in key else key


def windows(begin: date, end: date, span: int = MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """[begin, end]를 양 끝 포함 span일 이하 구간으로 나눔."""
    out, cur = [], begin
    while cur <= end:
        stop = min(cur + timedelta(days=span - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def rows_of(payload) -> list[dict]:
    """응답 → 공고 줄. 'data'가 없으면 오류(빈 결과로 삼키면 놓친 줄 모름)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        keys = sorted(payload)[:6] if isinstance(payload, dict) else type(payload).__name__
        raise FetchError(f"응답에 data 목록이 없음 (받은 것: {keys})")
    return [r for r in payload["data"] if isinstance(r, dict)]


def fetch(key: str, begin: date, end: date, company: str | None = None, session=None) -> list[dict]:
    import requests

    params = {"apiKey": key, "noticeBeginDate": begin.strftime("%Y%m%d"), "noticeEndDate": end.strftime("%Y%m%d"),
              "returnType": "json"}
    if company:
        params["companyId"] = company
    try:
        res = (session or requests).get(API_URL, params=params, timeout=60)
    except requests.RequestException as err:
        raise FetchError(f"요청 실패: {str(err).replace(key, '***')}") from err
    if res.status_code == 404:
        return []  # 그 기간 공고 없음(사양)
    if res.status_code != 200:
        raise FetchError(f"HTTP {res.status_code}: {res.text[:200]}")
    try:
        return rows_of(res.json())
    except ValueError as err:
        raise FetchError(f"JSON이 아님: {res.text[:200]}") from err


def _num(value) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def _when(value) -> str:
    """'20261124153000'·'2026-11-24 15:30:00' 등 → '2026-11-24 15:30'(모르면 그대로)."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) >= 12:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}"
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value or "")


def to_notice(row: dict):
    """API 한 줄 → 웹앱 Notice(수집 규칙을 그대로 걸 수 있게)."""
    from nego.models import Notice

    company = row.get("companyId") or ""
    work = WORK_TYPES.get(row.get("itemType") or "") or PURCHASE_TYPES.get(row.get("purchaseType") or "") or "미상"
    return Notice(
        work_type=work,
        notice_no=str(row.get("no") or ""),
        notice_ord="000",
        title=str(row.get("name") or ""),
        notice_institution=COMPANY_NAMES.get(company, company) or None,
        demand_institution=row.get("placeName") or None,
        award_method=BID_TYPES.get(row.get("bidType") or "", row.get("bidType")),
        contract_method=COMPETITION.get(row.get("competitionType") or "", row.get("competitionType")),
        # 자재구매는 추정가격만, 공사용역은 추정금액만 채워 옴(사양 실측)
        estimated_price=_num(row.get("presumedPrice")) or _num(row.get("presumedAmount")),
        posted_at=_when(row.get("noticeDate")) or None,
        qualification_deadline=_when(row.get("bidAttendReqCloseDatetime")) or None,
        bid_deadline=_when(row.get("endDatetime")) or None,
    )


def judge(rows: list[dict], screen_config) -> list[dict]:
    """줄마다 지금 수집 규칙 판정. 같은 공고번호는 한 번."""
    from nego.screen import screen

    out, seen = [], set()
    for row in rows:
        notice = to_notice(row)
        if notice.notice_no and notice.notice_no in seen:
            continue
        seen.add(notice.notice_no)
        res = screen(notice, screen_config)
        if res.matched:
            verdict = "검토필요" if res.confidence == "검토필요" else "후보"
            why = ", ".join(res.matched_keywords) + (f" (제외 키워드 '{res.review_exclude}'도 있음)" if res.review_exclude else "")
        else:
            verdict = "빠짐"
            why = f"{res.excluded_by}: {res.excluded_reason}" if res.excluded_by == "제외키워드" else (res.excluded_by or "")
        restrict = " ".join(str(row.get("bidAttendRestrict") or "").split())
        out.append({
            "회사": notice.notice_institution or "",
            "발주기관": notice.demand_institution or "",
            "업무": notice.work_type,
            "계약방법": notice.contract_method or "",
            "낙찰방법": notice.award_method or "",
            "상태": STATES.get(row.get("progressState") or "", row.get("progressState") or ""),
            "공고일": notice.posted_at or "",
            "참가신청 마감": notice.qualification_deadline or "",
            "입찰 마감": notice.bid_deadline or "",
            "금액": int(notice.estimated_price) if notice.estimated_price else "",
            "공고번호": notice.notice_no,
            "공고명": notice.title,
            "판정": verdict,
            "이유": why,
            "참가자격(앞부분)": restrict[:200],
        })
    return out


def field_fill(rows: list[dict]) -> dict[str, int]:
    return {f: sum(1 for r in rows if str(r.get(f) or "").strip()) for f in CHECK_FIELDS}


def summary(judged: list[dict], rows: list[dict], period: str) -> str:
    lines = [f"전력그룹사 전자입찰 공고 점검 — {period}", f"받은 공고 {len(judged)}건(응답 줄 {len(rows)}개)", ""]
    for title, key in (("회사별", "회사"), ("업무별", "업무"), ("낙찰방법별", "낙찰방법"), ("상태별", "상태")):
        counts = Counter(j[key] or "(빈칸)" for j in judged)
        lines.append(f"{title}: " + " · ".join(f"{k} {v}" for k, v in counts.most_common()))
    verdicts = Counter(j["판정"] for j in judged)
    lines += ["", f"지금 수집 규칙 적용: 후보 {verdicts.get('후보', 0)} · 검토필요 {verdicts.get('검토필요', 0)} · 빠짐 {verdicts.get('빠짐', 0)}"]
    words = Counter(j["이유"].split(": ", 1)[1] for j in judged if j["이유"].startswith("제외키워드: "))
    if words:
        lines.append("제외 키워드로 빠진 것: " + " · ".join(f"{k} {v}" for k, v in words.most_common(10)))
    picked = [j for j in judged if j["판정"] != "빠짐"]
    if picked:
        lines += ["", "걸린 공고:"]
        for j in sorted(picked, key=lambda x: x["입찰 마감"]):
            money = f"{j['금액']:,}원" if j["금액"] != "" else "금액 없음"
            lines.append(f"  [{j['판정']}] {j['회사']} · {j['업무']} · {j['낙찰방법'] or '-'} · 마감 {j['입찰 마감'] or '-'} · {money}")
            lines.append(f"      {j['공고명']} ({j['공고번호']}) — {j['이유']}")
    fill = field_fill(rows)
    if rows:
        lines += ["", "칸 채움(응답 줄 중 값이 있는 수): " + " · ".join(f"{k} {v}" for k, v in fill.items())]
    return "\n".join(lines)


def write_csv(judged: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        cols = list(judged[0]) if judged else ["공고번호", "공고명", "판정"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(judged)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="전력그룹사 전자입찰(SRM) 공고를 받아 지금 수집 규칙에 걸리는 건수를 셈")
    p.add_argument("--days", type=int, default=30, help="오늘 포함 최근 며칠 공고(기본 30)")
    p.add_argument("--company", action="append", help="회사 코드(COM01 한국전력공사 등, 여러 번 가능 — 빼면 전부)")
    p.add_argument("--input", type=Path, help="저장해 둔 응답 JSON으로(인터넷 없이)")
    p.add_argument("--out", type=Path, default=ROOT / "output", help="결과 폴더(기본 output)")
    p.add_argument("--save-raw", action="store_true", help="받은 응답 그대로 저장(담당자 연락처 포함 — 공개 금지)")
    args = p.parse_args(argv)

    end = date.today()
    begin = end - timedelta(days=max(args.days, 1) - 1)
    if args.input:
        rows = rows_of(json.loads(args.input.read_text(encoding="utf-8")))
    else:
        key = api_key()
        if not key:
            print(f"전력데이터 개방포털 인증키가 없습니다 — {KEY_NAME} 환경변수나 레포 루트 .env에 넣어 주세요.", file=sys.stderr)
            return 2
        rows = []
        for b, e in windows(begin, end):
            for company in args.company or [None]:
                try:
                    got = fetch(key, b, e, company)
                except FetchError as err:
                    print(f"⚠ {b}~{e} {company or '전체'}: {err}", file=sys.stderr)
                    return 1
                print(f"  {b} ~ {e} {company or '전체'}: {len(got)}줄", file=sys.stderr)
                rows += got

    from nego.config import load_config

    judged = judge(rows, load_config().screen)
    period = f"저장한 응답 {args.input.name}" if args.input else f"공고일 {begin} ~ {end}"
    print(summary(judged, rows, period))
    stamp = date.today().isoformat()
    out_csv = args.out / f"전력그룹사_공고점검_{stamp}.csv"
    write_csv(judged, out_csv)
    print(f"\nCSV: {out_csv}")
    if args.save_raw and not args.input:
        raw = args.out / f"전력그룹사_응답_{stamp}.json"
        raw.write_text(json.dumps({"data": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"받은 응답: {raw} (담당자 연락처 포함 — 공개 금지)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
