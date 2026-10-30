"""개찰 결과 목록에서 '지일 분야 + 참가업체 적은' 공고를 골라 받을 목록(CSV)을 만든다 — 지일이 투찰하지 않은 공고까지.

추천 투찰가 표본 늘리기(2026-10-28 사용자 결정 ①): 지일 투찰 이력만으로는 우리 방식이 차이를 낼 수 있는 소규모 경쟁 공고가 88건뿐이라,
나라장터에 공개된 개찰 결과에서 같은 분야(공고명 키워드 — `nego.bidfield`)의 참가 100곳 미만 공고를 더 모은다.
**서버 PC에서 실행**한다(결과 목록·개찰 자료는 저장소에 올리지 않음 — output/은 깃 제외).

    py scripts/find_bid_targets.py --from 2024-01-01 --to 2026-10-27          # 목록 만들기 → output/bid_targets_others.csv
    py scripts/fetch_bid_results.py output/bid_targets_others.csv --out output/bid_results_others.jsonl   # 개찰 결과 받기

받은 `bid_results_others.jsonl`은 이름이 bid_results*.jsonl이라 추천·검증(`nego.bidbacktest`·`nego.bidtrend`)·웹앱이 그대로 같이 읽는다.
이미 받은 공고(output/의 bid_results*.jsonl에 있는 공고번호)는 목록에서 뺀다. 하루 호출 한도(resultCode=22)에 걸리면 모은 데까지 저장하고 멈춘다.

**입찰만**(2026-10-30 사용자 요청, 기본): 9월 200건을 받았더니 협상·하한선 없는 입찰이 대부분이라 4건만 쓸 수 있었다. 그래서 고른 공고의
입찰공고(등록일 기간 — 개찰 기간 앞 `NOTICE_LOOKBACK_DAYS`일부터)를 받아 낙찰방법·하한율·예가방법을 보고 협상·규격가격동시입찰·하한율 없음·
복수예가 아님을 뺀다(`bid_only_reason`). 공고를 못 찾은 것도 뺀다. 수의 견적(하한율 있는 소액수의)은 남긴다. 예전처럼 전부: `--all-methods`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_bid_results import QUOTA_CODE, service_key  # noqa: E402

from nego import fields as F  # noqa: E402
from nego.bidfield import NO_TITLE, OTHER, classify  # noqa: E402
from nego.http_client import ApiConfig, ApiError, ApiResultError, DataGoKrClient  # noqa: E402

LIST_OPS = {"공사": F.SCSBID_OPERATIONS["개찰결과/공사"], "용역": F.SCSBID_OPERATIONS["개찰결과/용역"],
            "물품": F.SCSBID_OPERATIONS["개찰결과/물품"]}
HEAD = ["공고번호", "업무", "분야", "참가", "개찰일", "공고명"]
NOTICE_LOOKBACK_DAYS = 60  # 공고는 대개 개찰 1~4주 전에 등록 — 입찰공고 목록을 개찰 기간 시작 60일 전부터 받음
MIN_LOWER_RATE = 50  # 이보다 낮으면 하한선 없는 입찰로 봄(`bidrecommend.MIN_LOWER_RATE`와 같음)


def _int(value) -> int | None:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def windows(begin: date, end: date, days: int) -> list[tuple[str, str]]:
    """[begin, end]를 days일씩 — 나라장터 조회기간 형식(YYYYMMDDHHMM)."""
    out, cur = [], begin
    while cur <= end:
        last = min(end, cur + timedelta(days=days - 1))
        out.append((f"{cur:%Y%m%d}0000", f"{last:%Y%m%d}2359"))
        cur = last + timedelta(days=1)
    return out


def pick(rows: list[dict], work: str, max_bidders: int, fields: set[str] | None) -> dict[str, dict]:
    """개찰 결과 목록 행 → {공고번호: 목록 줄}. 같은 공고는 마지막 차수만, 참가 1~max_bidders곳, 분야가 기타·공고명 없음이면 뺌
    (fields를 주면 그 분야만)."""
    out: dict[str, dict] = {}
    for row in rows:
        no, title = str(row.get("bidNtceNo") or "").strip(), str(row.get("bidNtceNm") or "").strip()
        count = _int(row.get("prtcptCnum"))
        field = classify(title)
        if not no or not count or count > max_bidders or field in (OTHER, NO_TITLE):
            continue
        if fields and field not in fields:
            continue
        ord_ = str(row.get("bidNtceOrd") or "").strip()
        old = out.get(no)
        if old and old["차수"] >= ord_:
            continue
        out[no] = {"공고번호": f"{no}-{ord_}" if ord_ else no, "업무": work, "분야": field, "참가": count,
                   "개찰일": str(row.get("opengDt") or "")[:10], "공고명": title, "차수": ord_}
    return out


def bid_only_reason(row: dict | None) -> str | None:
    """입찰공고 행 → 뺄 이유(투찰 계산에 못 쓰는 공고), 쓸 수 있으면 None. 낙찰방법 협상·규격가격동시입찰, 하한율 없음, 복수예가 아님."""
    if row is None:
        return "입찰공고 못 찾음"
    award = re.sub(r"\s+", "", str(row.get("sucsfbidMthdNm") or ""))
    if "협상" in award:
        return "협상"
    if award.startswith("규격가격"):
        return "규격가격동시입찰"
    rate = _float(row.get("sucsfbidLwltRate"))
    if not rate or rate < MIN_LOWER_RATE:
        return "하한율 없음"
    price = str(row.get("prearngPrceDcsnMthdNm") or "")
    if price and "복수" not in price:
        return "복수예가 아님"
    return None


def _float(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def notice_rows(client: DataGoKrClient, works: list[str], begin: date, end: date) -> tuple[dict[str, dict], bool]:
    """업무별 입찰공고 목록(등록일 begin − NOTICE_LOOKBACK_DAYS ~ end) → {공고번호: 마지막 차수 행}, 한도에 걸려 멈췄나."""
    start = begin - timedelta(days=NOTICE_LOOKBACK_DAYS)
    out: dict[str, dict] = {}
    for work in works:
        for a, b in windows(start, end, 30):
            try:
                rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[work],
                                              {"inqryDiv": "1", "inqryBgnDt": a, "inqryEndDt": b}, f"입찰공고 {work} {a[:8]}")
            except ApiResultError as err:
                if err.result_code == QUOTA_CODE:
                    return out, True
                print(f"  입찰공고 {work} {a[:8]}~{b[:8]} 실패: {str(err)[:200]}")
                continue
            except ApiError as err:
                print(f"  입찰공고 {work} {a[:8]}~{b[:8]} 실패: {str(err)[:200]}")
                continue
            for row in rows:
                no = str(row.get("bidNtceNo") or "").strip()
                if no and (no not in out or str(out[no].get("bidNtceOrd") or "") <= str(row.get("bidNtceOrd") or "")):
                    out[no] = row
            print(f"  입찰공고 {work} {a[:8]}~{b[:8]}: {len(rows)}건")
    return out, False


def already_fetched(directory: Path) -> set[str]:
    """output/의 bid_results*.jsonl에 이미 있는(오류 없이 받은) 공고번호."""
    done: set[str] = set()
    for path in directory.glob("bid_results*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("error") and rec.get("no"):
                done.add(str(rec["no"]).split("-")[0])
    return done


def collect(client: DataGoKrClient, base_url: str, works: list[str], begin: date, end: date, days: int,
            max_bidders: int, fields: set[str] | None) -> tuple[dict[str, dict], bool, Counter]:
    """기간·업무별 개찰 결과 목록을 받아 고른다. (고른 공고, 한도에 걸려 멈췄나, 업무별 받은 행 수)."""
    found: dict[str, dict] = {}
    seen: Counter = Counter()
    for work in works:
        for a, b in windows(begin, end, days):
            try:
                rows = client.fetch_all_pages(base_url, LIST_OPS[work], {"inqryDiv": "1", "inqryBgnDt": a, "inqryEndDt": b},
                                              f"개찰결과 {work} {a[:8]}")
            except ApiResultError as err:
                if err.result_code == QUOTA_CODE:
                    return found, True, seen
                print(f"  {work} {a[:8]}~{b[:8]} 실패: {str(err)[:200]}")
                continue
            except ApiError as err:
                print(f"  {work} {a[:8]}~{b[:8]} 실패: {str(err)[:200]}")
                continue
            seen[work] += len(rows)
            for no, item in pick(rows, work, max_bidders, fields).items():
                if no not in found or found[no]["차수"] < item["차수"]:
                    found[no] = item
            print(f"  {work} {a[:8]}~{b[:8]}: 개찰 {len(rows)}건 · 지금까지 고른 공고 {len(found)}건")
    return found, False, seen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개찰 결과 목록에서 지일 분야·참가 적은 공고 고르기(받을 목록 CSV)")
    parser.add_argument("--from", dest="begin", default="", help="시작일 YYYY-MM-DD(기본 1년 전)")
    parser.add_argument("--to", dest="end", default="", help="끝일 YYYY-MM-DD(기본 어제)")
    parser.add_argument("--works", default="공사,용역,물품", help="업무(쉼표로) — 기본 공사,용역,물품")
    parser.add_argument("--max-bidders", type=int, default=99, help="참가업체 수 상한(기본 99 — 100곳 미만)")
    parser.add_argument("--fields", default="", help="이 분야만(쉼표로, config/bid_fields.json 이름) — 기본: 기타 빼고 전부")
    parser.add_argument("--days", type=int, default=7, help="한 번에 조회할 날 수(기본 7 — 조회기간 오류가 나면 줄이기)")
    parser.add_argument("--dir", default=str(ROOT / "output"), help="이미 받은 개찰 자료 폴더(기본 output)")
    parser.add_argument("--out", default="", help="목록 CSV(기본 <dir>/bid_targets_others.csv)")
    parser.add_argument("--all-methods", action="store_true",
                        help="협상·하한율 없는 공고도 남김(예전 방식) — 기본은 투찰 계산에 쓸 수 있는 입찰만")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    key = service_key()
    if not key:
        print("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.", file=sys.stderr)
        return 1
    today = date.today()
    begin = datetime.strptime(args.begin, "%Y-%m-%d").date() if args.begin else today - timedelta(days=365)
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today - timedelta(days=1)
    works = [w.strip() for w in args.works.split(",") if w.strip() in LIST_OPS]
    fields = {f.strip() for f in args.fields.split(",") if f.strip()} or None
    directory = Path(args.dir)
    out = Path(args.out) if args.out else directory / "bid_targets_others.csv"

    client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=30.0, max_retries=3))
    client.config.num_of_rows = 999
    base_url = os.environ.get("SCSBID_BASE_URL", "").strip() or F.SCSBID_BASE_URL
    print(f"개찰 결과 목록 {begin} ~ {end} · {', '.join(works)} · 참가 {args.max_bidders}곳 이하 · 분야 {', '.join(sorted(fields)) if fields else '기타 빼고 전부'}")
    found, stopped, seen = collect(client, base_url, works, begin, end, args.days, args.max_bidders, fields)
    dropped: Counter = Counter()
    if stopped and not args.all_methods:  # 개찰 목록도 다 못 받음 — 거르지 않은 반쪽 목록은 안 만듦
        print("\n오늘 호출 한도에 걸려 개찰 결과 목록을 다 못 받았습니다 — 목록을 만들지 않았습니다. 내일 같은 명령을 다시 실행하세요.")
        return 0
    if not args.all_methods and found:
        print(f"\n입찰만 남기기 — 입찰공고 목록(등록일 {begin - timedelta(days=NOTICE_LOOKBACK_DAYS)} ~ {end})에서 낙찰방법·하한율 확인")
        notices, stopped = notice_rows(client, sorted({r["업무"] for r in found.values()}), begin, end)
        if stopped:  # 거르지 못한 목록을 쓰면 협상까지 받게 됨 — 목록을 안 만들고 멈춤
            print("\n오늘 호출 한도에 걸려 입찰만 거르기를 못 했습니다 — 목록을 만들지 않았습니다. 내일 같은 명령을 다시 실행하세요.")
            return 0
        for no in list(found):
            why = bid_only_reason(notices.get(no))
            if why:
                dropped[why] += 1
                del found[no]

    done = already_fetched(directory)
    rows = sorted((r for no, r in found.items() if no not in done), key=lambda r: r["개찰일"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for r in rows:
            w.writerow([r[k] for k in HEAD])
    print(f"\n받은 개찰 행: " + " · ".join(f"{k} {n}건" for k, n in seen.items()))
    print(f"고른 공고 {len(found)}건 (이미 받은 {len(found) - len(rows)}건 뺌) → {out}  {len(rows)}건")
    print("분야별: " + " · ".join(f"{k} {n}" for k, n in Counter(r["분야"] for r in rows).most_common()))
    if dropped:
        print("입찰이 아니라 뺀 공고: " + " · ".join(f"{k} {n}" for k, n in dropped.most_common()))
    if stopped:
        print("\n오늘 호출 한도에 걸려 목록을 다 못 만들었습니다 — 내일 같은 명령을 다시 실행하세요(목록은 새로 만듦).")
    print(f"\n다음: py scripts/fetch_bid_results.py {out} --out {directory / 'bid_results_others.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
