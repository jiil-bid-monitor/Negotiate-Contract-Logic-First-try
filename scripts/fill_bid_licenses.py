"""이미 받은 과거 개찰 자료(output/bid_results*.jsonl)에 공고별 **면허제한 업종**을 채운다 — 서버 PC에서 실행.

2026-10-30 사용자 결정: 추천 칸에서 업종(21라인 '업종구분')을 쓸지 보려고 과거 공고에도 업종을 채워 본다. **한 달치 시험부터**.

    py scripts/fill_bid_licenses.py --month 2025-09     # 시험: 2025년 9월에 개찰한 공고만
    py scripts/fill_bid_licenses.py --all               # 전부(달마다 저장, 하루 한도면 멈춤 — 다시 실행하면 이어서)

면허제한 오퍼레이션은 공고번호 조회(inqryDiv=2)를 받지 않는다(2026-10-15 실측 resultCode=08). 그래서 **등록일 기간**으로 나라장터 전체
면허제한 행을 받아 공고번호로 맞춘다. 과거 자료엔 등록일이 없어, 개찰한 달과 그 앞 두 달(`LOOKBACK_MONTHS`)에 등록된 행을 본다.
기간은 반달씩 나눠 받고(한 번에 30일 넘으면 거부됨) 같은 반달은 다시 받지 않는다.

각 줄 `notice`에 `licenses`(제한그룹 → 행 → 이름들, 첫 이름이 대표 업종 "실내건축공사업/4990"), `license_found`(찾았나),
`license_checked`(찾아본 시각)를 넣는다. 못 찾은 공고는 면허제한이 없는 공고(물품·용역에 많음)이거나 더 일찍 등록된 공고다.
처음 한 번 파일마다 원본을 `<이름>_채우기전.jsonl`로 남긴다. 결과 파일은 저장소에 올리지 않는다(공개 금지).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_bid_results import QUOTA_CODE, _write_lines, service_key  # noqa: E402
from nego import fields as F  # noqa: E402
from nego.bidrecommend import _opened_at, history_paths  # noqa: E402
from nego.http_client import ApiConfig, ApiError, ApiResultError, DataGoKrClient  # noqa: E402
from nego.qualify import group_license_rows  # noqa: E402

LOOKBACK_MONTHS = 2  # 개찰한 달 앞으로 몇 달 등록분까지 볼지 — 대부분 공고는 개찰 1~4주 전에 등록
ROWS_PER_PAGE = 999


def month_of(rec: dict) -> str | None:
    """개찰한 달 'YYYY-MM' — 공고 정보 개찰일 → 개찰 결과 행의 날짜 칸(`bidrecommend._opened_at`)."""
    digits = re.sub(r"\D", "", _opened_at(rec, (rec.get("bidders") or []) + (rec.get("prices") or [])) or "")
    return f"{digits[:4]}-{digits[4:6]}" if len(digits) >= 6 else None


def needs_license(rec: dict) -> bool:
    if rec.get("error") or not (rec.get("prices") or rec.get("bidders")):
        return False
    return not (rec.get("notice") or {}).get("license_checked")


def halves(month: str) -> list[tuple[str, str]]:
    """'2025-09' → 그 달과 앞 LOOKBACK_MONTHS달을 반달씩 [(시작, 끝)] — API 형식 YYYYMMDDHHMM."""
    year, mon = map(int, month.split("-"))
    out = []
    for back in range(LOOKBACK_MONTHS, -1, -1):
        y, m = year, mon - back
        while m < 1:
            y, m = y - 1, m + 12
        first = date(y, m, 1)
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        mid = date(y, m, 15)
        out.append((f"{first:%Y%m%d}0000", f"{mid:%Y%m%d}2359"))
        out.append((f"{mid + timedelta(days=1):%Y%m%d}0000", f"{last:%Y%m%d}2359"))
    return out


def licenses_json(groups) -> list[list[list[str]]]:
    """LicenseGroup 목록 → [그룹[행[이름…]]] — 행이 없으면 allowed_names 전체가 한 행."""
    return [[list(row) for row in (g.rows or [g.allowed_names])] for g in groups]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="과거 개찰 자료에 면허제한 업종 채우기(시험: --month)")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--month", action="append", help="개찰한 달 YYYY-MM(여러 번 줄 수 있음) — 시험용")
    which.add_argument("--all", action="store_true", help="아직 안 찾아본 공고 전부(달마다 저장)")
    parser.add_argument("--dir", default=str(ROOT / "output"), help="과거 개찰 자료 폴더(기본 output/)")
    parser.add_argument("--sleep", type=float, default=0.3, help="조회 사이 쉬는 초(기본 0.3)")
    args = parser.parse_args(argv)

    key = service_key()
    if not key:
        print("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.", file=sys.stderr)
        return 1
    paths = history_paths(Path(args.dir))
    paths = [p for p in paths if p.suffix == ".jsonl"]
    if not paths:
        print(f"과거 개찰 자료(bid_results*.jsonl)가 없습니다: {args.dir}", file=sys.stderr)
        return 1

    client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=60.0, max_retries=3))
    client.config.num_of_rows = ROWS_PER_PAGE
    fill_licenses(client, paths, None if args.all else set(args.month), args.sleep)
    return 0


def fill_licenses(client, paths: list[Path], months: set[str] | None, sleep: float = 0.3) -> dict:
    """paths의 개찰 자료 중 아직 안 찾아본 공고(months가 있으면 그 달 개찰분만)에 면허제한 업종을 채운다.
    → {total, found, calls, rows, stopped}"""
    files: dict[Path, tuple[list[str], list[dict]]] = {}
    by_month: dict[str, list[tuple[Path, int]]] = {}
    no_month = 0
    for path in paths:
        raw = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        recs = [json.loads(line) for line in raw]
        files[path] = (raw, recs)
        for i, rec in enumerate(recs):
            if not needs_license(rec):
                continue
            month = month_of(rec)
            if month is None:
                no_month += 1
            elif months is None or month in months:
                by_month.setdefault(month, []).append((path, i))
    total = sum(len(v) for v in by_month.values())
    print(f"자료 {len(paths)}개 파일 · 채울 공고 {total}건 ({len(by_month)}개월)" + (f" · 개찰일 몰라 뺀 공고 {no_month}건" if no_month else ""))
    if not total:
        return {"total": 0, "found": 0, "calls": 0, "rows": 0, "stopped": False}
    for path, (raw, _) in files.items():
        backup = path.with_name(path.stem + "_채우기전" + path.suffix)
        if not backup.exists():
            backup.write_text("".join(line + "\n" for line in raw), encoding="utf-8")

    cache: dict[tuple[str, str], dict] = {}  # 반달 → 공고번호별 면허제한 그룹
    calls = rows_total = found_all = 0
    old_found = old_total = 0
    names = Counter()
    stopped = False
    started = time.time()
    for month in sorted(by_month):
        month_started = time.time()
        merged: dict = {}
        try:
            for span in halves(month):
                if span not in cache:
                    raw_items = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.LICENSE_LIMIT_OPERATION,
                                                       {"inqryDiv": "1", "inqryBgnDt": span[0], "inqryEndDt": span[1]},
                                                       f"면허제한 {span[0][:8]}~{span[1][:8]}")
                    calls += max(1, math.ceil(len(raw_items) / ROWS_PER_PAGE))
                    rows_total += len(raw_items)
                    cache[span] = group_license_rows(raw_items)
                    time.sleep(sleep)
                merged.update(cache[span])  # 뒤 반달(최신 등록) 것이 앞 것을 덮음 — 정정공고는 최신 차수
        except ApiResultError as err:
            if err.result_code == QUOTA_CODE:
                print(f"\n오늘 호출 한도에 걸려 멈춥니다({month} 전까지 저장). 내일 같은 명령을 다시 실행하면 이어서 채웁니다.")
                stopped = True
                break
            print(f"\n{month} 조회 실패 — 멈춥니다: {err}", file=sys.stderr)
            stopped = True
            break
        except ApiError as err:
            print(f"\n{month} 조회 실패 — 멈춥니다: {err}", file=sys.stderr)
            stopped = True
            break
        for old in [k for k in cache if k not in halves(month)]:  # 다음 달에 다시 쓸 반달만 남김
            del cache[old]
        found = 0
        now = datetime.now().isoformat(timespec="seconds")
        for path, i in by_month[month]:
            raw, recs = files[path]
            rec = recs[i]
            no = str(rec.get("no") or rec.get("input", "").split("-")[0]).strip()
            groups = merged.get(no)
            is_old = no.isdigit()
            old_total += is_old
            info = rec.setdefault("notice", {})
            info["license_checked"] = now
            info["license_found"] = bool(groups)
            if groups:
                found += 1
                old_found += is_old
                info["licenses"] = licenses_json(groups)
                for group in info["licenses"]:
                    for row in group:
                        names[row[0].split("/")[0]] += 1
            raw[i] = json.dumps(rec, ensure_ascii=False)
        found_all += found
        for path in {p for p, _ in by_month[month]}:
            _write_lines(path, files[path][0])
        print(f"  {month}: 공고 {len(by_month[month])}건 중 업종 찾음 {found}건 · {time.time() - month_started:.0f}초")

    spent = time.time() - started
    print(f"\n끝 — 업종 찾음 {found_all}/{total}건 · 조회 약 {calls}번(받은 행 {rows_total:,}개) · {spent / 60:.1f}분")
    if old_total:
        print(f"옛 공고번호(2023년 이전 모양) {old_total}건 중 찾음 {old_found}건 — 0이면 옛 공고는 이 API로 못 채움")
    if names:
        print("자주 나온 대표 업종: " + ", ".join(f"{n} {c}" for n, c in names.most_common(12)))
    if len(by_month) == 1 and calls:
        print(f"참고: 이 속도면 한 달에 조회 약 {calls}번·{spent / 60:.1f}분 — 반달은 이웃 달과 겹쳐 전체(--all)에선 달마다 약 2번만 새로 받음")
    return {"total": total, "found": found_all, "calls": calls, "rows": rows_total, "stopped": stopped}


if __name__ == "__main__":
    sys.exit(main())
