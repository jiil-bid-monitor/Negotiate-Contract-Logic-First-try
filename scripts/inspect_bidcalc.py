"""공고 1건의 투찰 계산기 조회 결과 + 원본 기관·결정기준 칸 + 개찰 뒤 예비가격 15개를 찍는다(1회성 진단, 2026-10-23).

    NARA_SERVICE_KEY=... python scripts/inspect_bidcalc.py R26BK01672194
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import bidcalc  # noqa: E402
from nego import fields as F  # noqa: E402
from nego.http_client import ApiConfig, ApiError, DataGoKrClient  # noqa: E402

KEYS = ("Instt", "sucsfbid", "prearng", "Rng", "bidwinr", "cntrct", "bidNtceNm", "bidNtceOrd", "presmpt", "PrdprcNum")


def main(notice_no: str) -> int:
    key = os.environ.get("NARA_SERVICE_KEY", "").strip()
    client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=30.0, max_retries=3))
    params = {"inqryDiv": "2", "bidNtceNo": notice_no}

    print("── 투찰 계산기 조회(bidcalc.lookup) ──")
    out = bidcalc.lookup(client, notice_no)
    print(json.dumps(out, ensure_ascii=False, indent=1))

    work_type = out.get("work_type")
    print("\n── 본공고 원본 칸(기관·결정기준·예가) ──")
    for wt in ([work_type] if work_type else bidcalc.WORK_TYPES):
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[wt], params, f"공고/{wt}")
        except ApiError as err:
            print(wt, "오류", err)
            continue
        for row in rows:
            print(wt, json.dumps({k: v for k, v in row.items() if any(s in k for s in KEYS)}, ensure_ascii=False))

    print("\n── 기초금액 원본 칸 ──")
    for wt in ([work_type] if work_type else bidcalc.WORK_TYPES):
        op = bidcalc.BASIS_OPERATIONS.get(wt)
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, op, params, f"기초금액/{wt}")
        except ApiError as err:
            print(wt, "오류", err)
            continue
        for row in rows:
            print(wt, json.dumps(row, ensure_ascii=False))

    print("\n── 개찰 뒤 예비가격 상세 ──")
    base = os.environ.get("SCSBID_BASE_URL", F.SCSBID_BASE_URL)
    ops = {"용역": F.SCSBID_OPERATIONS["예비가격/용역"], "공사": F.SCSBID_OPERATIONS["예비가격/공사"],
           "물품": "getOpengResultListInfoThngPreparPcDetail"}
    for wt in ([work_type] if work_type else bidcalc.WORK_TYPES):
        try:
            rows = client.fetch_all_pages(base, ops[wt], params, f"예비가격/{wt}")
        except ApiError as err:
            print(wt, "오류", err)
            continue
        print(f"{wt}: {len(rows)}행")
        if rows:
            print("칸:", list(rows[0].keys()))
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        prices = []
        for row in rows:
            try:
                prices.append((float(row.get("bsisPlnprc")), float(row.get("bssamt")), row.get("drwtYn"), row.get("drwtNum"),
                               row.get("compnoRsrvtnPrceSno")))
            except (TypeError, ValueError):
                pass
        for p, b, y, n, sno in sorted(prices, key=lambda x: str(x[4]).zfill(3)):
            print(f"  번호 {sno}: {p:,.0f}  ({(p / b - 1) * 100:+.4f}%)  추첨 {y}  뽑힌 횟수 {n}")
        if prices:
            pct = [(p / b - 1) * 100 for p, b, *_ in prices]
            print(f"  범위: {min(pct):+.4f}% ~ {max(pct):+.4f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].split("-")[0].strip()))
