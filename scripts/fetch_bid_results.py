"""공고번호 목록 → 나라장터 개찰결과(참가업체 전체 순위·금액 + 예비가격 15개)와 낙찰방법을 받아 한 줄에 한 공고씩(JSONL) 저장한다.

추천 사정률용 "투찰 경향" 분석 자료(2026-10-23 사용자 결정). **서버 PC에서 실행**한다 — 공고번호 목록(지일 투찰 이력)은
공개 저장소·GitHub Actions에 올리지 않기 위해서다. 목록 파일과 결과 파일은 저장소에 커밋하지 않는다(결과 기본 위치 output/은 깃 제외).

    py scripts/fetch_bid_results.py 목록.csv --limit 5      # 시험 5건
    py scripts/fetch_bid_results.py 목록.csv                # 나머지 전부(이미 받은 공고는 건너뜀 — 끊겨도 다시 실행하면 이어서)
    py scripts/fetch_bid_results.py 목록.csv --notice-only  # 낙찰방법만(본공고 조회 1~3번) → output/bid_notices.jsonl

목록.csv: 첫 칸 공고번호(예: R26BK01758791-000, 20201217355-00), 둘째 칸 업무(공사·시설·용역·물품, 비면 셋 다 시도).
투찰 이력에서 목록 만들기: `py scripts/make_bid_targets.py` → data/bid_results_targets.csv.
낙찰방법(`notice`)은 입찰공고정보서비스 본공고의 낙찰방법·계약방법·낙찰자 결정기준·하한율 — 협상 공고를 가려내는 데 쓴다.
인증키는 환경변수 NARA_SERVICE_KEY, 없으면 레포 루트 .env(webapp/start.sh가 만든 파일)에서 읽는다.
하루 호출 한도(resultCode=22)에 걸리면 받은 데까지 저장하고 멈춘다 — 다음 날 같은 명령을 다시 실행하면 된다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import fields as F  # noqa: E402
from nego.http_client import ApiConfig, ApiError, ApiResultError, DataGoKrClient  # noqa: E402

# 예비가격 상세 — 업무별 오퍼레이션(물품은 이름 추정, nego/bidprice.py와 같음)
PRICE_OPS = {
    "공사": F.SCSBID_OPERATIONS["예비가격/공사"],
    "용역": F.SCSBID_OPERATIONS["예비가격/용역"],
    "물품": "getOpengResultListInfoThngPreparPcDetail",
}
WORK_ALIASES = {"시설": "공사", "공사": "공사", "용역": "용역", "물품": "물품", "내자": "물품"}
# 본공고에서 남길 칸(이름 → 나라장터 칸)
NOTICE_FIELDS = {
    "award_method": "sucsfbidMthdNm",        # 낙찰방법(협상에의한계약·적격심사 …)
    "contract_method": "cntrctCnclsMthdNm",  # 계약방법(제한경쟁·수의 …)
    "award_std": "sucsfbidMthdAppStd",       # 낙찰자 결정기준
    "lower_rate": "sucsfbidLwltRate",        # 낙찰하한율
    "price_method": "prearngPrceDcsnMthdNm", # 예가방법(복수예가·단일예가 …)
    "title": "bidNtceNm",
}
QUOTA_CODE = "22"  # 서비스 요청제한횟수 초과(일일 한도)


def split_notice_no(text: str) -> tuple[str, str]:
    """'R26BK01758791-000' → ('R26BK01758791', '000'), '20201217355-00' → ('20201217355', '00'). 차수가 없으면 ''."""
    text = text.strip().split()[0] if text.strip() else ""
    no, _, ord_ = text.partition("-")
    return no, ord_


def service_key() -> str:
    key = os.environ.get("NARA_SERVICE_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("NARA_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
    # 인코딩된 키(%2B 등)를 넣어 두었으면 풀어서 씀 — 그대로면 SERVICE_KEY_IS_NOT_REGISTERED
    return urllib.parse.unquote(key) if "%" in key else key


def read_targets(path: Path) -> list[tuple[str, str]]:
    out = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip() or row[0].strip() in ("공고번호", "no"):
                continue
            out.append((row[0].strip(), row[1].strip() if len(row) > 1 else ""))
    return out


def done_numbers(out_path: Path) -> set[str]:
    """이미 받은(오류 없이 끝난) 공고번호 — 다시 실행하면 건너뛴다. 낙찰방법을 넣기 전(10/27 전)에 받은 줄도 끝난 것으로 본다
    — 그 공고들의 낙찰방법은 --notice-only로 따로 받는다."""
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("error"):
                done.add(rec["input"])
    return done


def _same_ord(rows: list[dict], ord_: str) -> list[dict]:
    if not ord_:
        return rows
    same = [r for r in rows if str(r.get("bidNtceOrd") or "").lstrip("0") == ord_.lstrip("0")]
    return same or rows


def fetch_notice(client: DataGoKrClient, no: str, ord_: str, work: str) -> tuple[dict | None, str]:
    """본공고(inqryDiv=2 + 공고번호)에서 낙찰방법 등 → (칸 모음 또는 None, 찾은 업무). 업무를 모르면 셋 다 시도."""
    params = {"inqryDiv": "2", "bidNtceNo": no}
    for w in [work] if work else list(F.BID_NOTICE_OPERATIONS):
        rows = _same_ord(client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[w], params, f"본공고 {no}"), ord_)
        if rows:
            row = max(rows, key=lambda r: str(r.get("bidNtceOrd") or ""))  # 차수가 안 맞으면 마지막 차수
            notice = {k: str(row.get(v) or "") for k, v in NOTICE_FIELDS.items()}
            notice["ord"] = str(row.get("bidNtceOrd") or "")
            return notice, w
    return None, work


def fetch_one(client: DataGoKrClient, base_url: str, notice_input: str, work: str, notice_only: bool = False) -> dict:
    no, ord_ = split_notice_no(notice_input)
    rec: dict = {"input": notice_input, "no": no, "ord": ord_, "work": WORK_ALIASES.get(work, ""), "fetched_at": datetime.now().isoformat(timespec="seconds")}
    rec["notice"], rec["work"] = fetch_notice(client, no, ord_, rec["work"])
    if notice_only:
        if rec["notice"] is None:
            rec["error"] = "나라장터에서 본공고를 찾지 못함"
        return rec
    params = {"inqryDiv": "2", "bidNtceNo": no}
    works = [rec["work"]] if rec["work"] else list(PRICE_OPS)
    prices: list[dict] = []
    for w in works:
        prices = client.fetch_all_pages(base_url, PRICE_OPS[w], params, f"예비가격 {no}")
        if prices:
            rec["work"] = w
            break
    rec["prices"] = _same_ord(prices, ord_)
    rec["bidders"] = _same_ord(client.fetch_all_pages(base_url, F.OPENING_BIDDERS_OPERATION, params, f"참가업체 {no}"), ord_)
    if not rec["prices"] and not rec["bidders"]:
        rec["error"] = "나라장터에서 개찰결과를 찾지 못함"
    return rec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공고번호 목록의 개찰결과(참가업체 전체·예비가격) 받기")
    parser.add_argument("targets", help="공고번호 목록 CSV (공고번호, 업무)")
    parser.add_argument("--out", default="", help="결과 파일(JSONL, 이어 쓰기 — 기본 output/bid_results.jsonl, --notice-only면 output/bid_notices.jsonl)")
    parser.add_argument("--notice-only", action="store_true", help="개찰결과 없이 낙찰방법(본공고)만 받기")
    parser.add_argument("--limit", type=int, default=0, help="이번에 받을 최대 공고 수(시험용, 0이면 전부)")
    parser.add_argument("--sleep", type=float, default=0.3, help="공고 사이 쉬는 초(기본 0.3)")
    args = parser.parse_args(argv)

    key = service_key()
    if not key:
        print("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.", file=sys.stderr)
        return 1
    out_path = Path(args.out or ROOT / "output" / ("bid_notices.jsonl" if args.notice_only else "bid_results.jsonl"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    targets = read_targets(Path(args.targets))
    done = done_numbers(out_path)
    todo = [t for t in targets if t[0] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"목록 {len(targets)}건 · 이미 받음 {len(targets) - len([t for t in targets if t[0] not in done])}건 · 이번에 받을 것 {len(todo)}건 → {out_path}")

    client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=30.0, max_retries=3))
    client.config.num_of_rows = 999
    base_url = os.environ.get("SCSBID_BASE_URL", "").strip() or F.SCSBID_BASE_URL
    ok = fail = 0
    started = time.time()
    with out_path.open("a", encoding="utf-8") as out:
        for i, (notice_input, work) in enumerate(todo, 1):
            try:
                rec = fetch_one(client, base_url, notice_input, work, args.notice_only)
            except ApiResultError as err:
                if err.result_code == QUOTA_CODE:
                    print(f"\n오늘 호출 한도에 걸려 멈춥니다({i - 1}건까지 저장). 내일 같은 명령을 다시 실행하면 이어서 받습니다.")
                    break
                rec = {"input": notice_input, "error": str(err)[:300]}
            except ApiError as err:
                rec = {"input": notice_input, "error": str(err)[:300]}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            if rec.get("error"):
                fail += 1
            else:
                ok += 1
            if i % 10 == 0 or i == len(todo):
                left = (time.time() - started) / i * (len(todo) - i)
                print(f"  {i}/{len(todo)} · 성공 {ok} · 실패 {fail} · 남은 시간 약 {left / 60:.0f}분")
            time.sleep(args.sleep)
    print(f"끝 — 성공 {ok}건, 실패 {fail}건. 결과 파일: {out_path}")
    if fail:
        print("실패한 공고는 다시 실행하면 한 번 더 시도합니다(성공한 공고만 건너뜀).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
