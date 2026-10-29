"""공고번호 목록 → 나라장터 개찰결과(참가업체 전체 순위·금액 + 예비가격 15개)를 받아 한 줄에 한 공고씩(JSONL) 저장한다.

추천 사정률용 "투찰 경향" 분석 자료(2026-10-23 사용자 결정). **서버 PC에서 실행**한다 — 공고번호 목록(지일 투찰 이력)은
공개 저장소·GitHub Actions에 올리지 않기 위해서다. 목록 파일과 결과 파일은 저장소에 커밋하지 않는다(결과 기본 위치 output/은 깃 제외).

    py scripts/fetch_bid_results.py 목록.csv --limit 5      # 시험 5건
    py scripts/fetch_bid_results.py 목록.csv                # 나머지 전부(이미 받은 공고는 건너뜀 — 끊겨도 다시 실행하면 이어서)

목록.csv: 첫 칸 공고번호(예: R26BK01758791-000, 20201217355-00), 둘째 칸 업무(공사·시설·용역·물품, 비면 셋 다 시도).
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
    """이미 받은(오류 없이 끝난) 공고번호 — 다시 실행하면 건너뛴다."""
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


def fetch_one(client: DataGoKrClient, base_url: str, notice_input: str, work: str) -> dict:
    no, ord_ = split_notice_no(notice_input)
    rec: dict = {"input": notice_input, "no": no, "ord": ord_, "work": WORK_ALIASES.get(work, ""), "fetched_at": datetime.now().isoformat(timespec="seconds")}
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
    elif rec["work"]:
        rec["notice"] = notice_info(client, no, rec["work"])
    return rec


def notice_info(client: DataGoKrClient, no: str, work: str) -> dict:
    """추천 사정률(`nego/bidrecommend.py`)에 필요한 공고 값 — 낙찰하한율·공고명(입찰공고) + 변동폭·A값(기초금액).
    하루 한도 오류는 그대로 올려 멈추게 하고, 그 밖의 오류는 빈 칸으로 둔다(개찰 결과는 그대로 저장)."""
    from nego.bidcalc import _latest, parse_basis

    info: dict = {}
    params = {"inqryDiv": "2", "bidNtceNo": no}
    try:
        row = _latest(client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[work], params, f"공고 {no}"), no)
        if row:
            info.update(title=str(row.get("bidNtceNm") or ""), lower_rate=row.get("sucsfbidLwltRate"),
                        opened_at=str(row.get("opengDt") or ""),
                        institution=str(row.get("dminsttNm") or row.get("ntceInsttNm") or ""))
        op = F.BASIS_AMOUNT_OPERATIONS.get(work)
        row = _latest(client.fetch_all_pages(F.BID_NOTICE_BASE_URL, op, params, f"기초금액 {no}"), no) if op else None
        if row:
            basis = parse_basis(row)
            info.update(range_lo=basis["range_lo"], range_hi=basis["range_hi"], a_value=basis["a_value"],
                        a_applies=basis["a_applies"])
    except ApiResultError as err:
        if err.result_code == QUOTA_CODE:
            raise
        info["error"] = str(err)[:200]
    except ApiError as err:
        info["error"] = str(err)[:200]
    return info


def needs_notice(rec: dict) -> bool:
    """공고 정보를 채워야 하는 줄 — 개찰 결과는 있는데 공고 정보가 없거나(10/28 전에 받은 줄) 하한율이 비고 아직 안 찾아본 줄."""
    if rec.get("error") or not (rec.get("prices") or rec.get("bidders")):
        return False
    info = rec.get("notice")
    return info is None or (not info.get("checked") and not info.get("lower_rate"))


def needs_a_value(rec: dict) -> bool:
    """A값(기초금액 API)이 빈 줄 — 하한선을 참가업체 금액으로 가늠하면 1순위를 확정할 수 없어(10/29) 다시 찾아볼 줄. 한 번 찾아보면 건너뜀."""
    if rec.get("error") or not (rec.get("prices") or rec.get("bidders")):
        return False
    info = rec.get("notice") or {}
    return info.get("a_value") is None and not info.get("a_checked")


def notice_info_any(client: DataGoKrClient, no: str, work: str) -> dict:
    """업무를 모르면(목록에 비어 있던 줄) 공사·용역·물품 순으로 공고를 찾아본다."""
    info: dict = {}
    for w in [work] if work in F.BID_NOTICE_OPERATIONS else list(F.BID_NOTICE_OPERATIONS):
        info = notice_info(client, no, w)
        if info.get("lower_rate") or info.get("title"):
            info["work"] = w
            break
    return info


def _write_lines(path: Path, lines: list[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    os.replace(tmp, path)  # 쓰다가 끊겨도 원래 파일은 그대로


def fill_notices(client: DataGoKrClient, path: Path, limit: int = 0, sleep: float = 0.3,
                 only_a: bool = False) -> tuple[int, int, bool]:
    """이미 받은 결과 파일에 공고 정보(하한율·공고명·변동폭·A값·개찰일)만 채운다 — 개찰 결과는 다시 받지 않음.
    처음 한 번 원본을 `<이름>_채우기전.jsonl`로 남기고, 100건마다 파일에 저장한다. (채운 수, 찾아본 수, 한도에 걸려 멈췄나)"""
    raw = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    backup = path.with_name(path.stem + "_채우기전" + path.suffix)
    if not backup.exists():
        backup.write_text("".join(line + "\n" for line in raw), encoding="utf-8")
    recs = [json.loads(line) for line in raw]
    todo = [i for i, rec in enumerate(recs) if (needs_a_value(rec) if only_a else needs_notice(rec))]
    if limit:
        todo = todo[:limit]
    print(f"결과 {len(recs)}줄 중 공고 정보를 채울 줄 {len(todo)}건 (원본 사본: {backup.name})")
    filled = tried = 0
    stopped = False
    started = time.time()
    for n, i in enumerate(todo, 1):
        rec = recs[i]
        try:
            info = notice_info_any(client, rec.get("no") or split_notice_no(rec.get("input", ""))[0], rec.get("work", ""))
        except ApiResultError as err:
            if err.result_code != QUOTA_CODE:
                raise
            print(f"\n오늘 호출 한도에 걸려 멈춥니다({n - 1}건까지 저장). 내일 같은 명령을 다시 실행하면 이어서 채웁니다.")
            stopped = True
            break
        if info.get("work") and not rec.get("work"):
            rec["work"] = info["work"]
        info.pop("work", None)
        info["checked"] = datetime.now().isoformat(timespec="seconds")
        if only_a:
            info["a_checked"] = info["checked"]
        rec["notice"] = {**(rec.get("notice") or {}), **{k: v for k, v in info.items() if v not in (None, "")}}
        raw[i] = json.dumps(rec, ensure_ascii=False)
        tried += 1
        filled += info.get("a_value") is not None if only_a else bool(info.get("lower_rate"))
        if n % 100 == 0:
            _write_lines(path, raw)
        if n % 20 == 0 or n == len(todo):
            left = (time.time() - started) / n * (len(todo) - n)
            print(f"  {n}/{len(todo)} · {'A값' if only_a else '하한율'} 찾음 {filled} · 남은 시간 약 {left / 60:.0f}분")
        time.sleep(sleep)
    _write_lines(path, raw)
    return filled, tried, stopped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공고번호 목록의 개찰결과(참가업체 전체·예비가격) 받기")
    parser.add_argument("targets", nargs="?", default="", help="공고번호 목록 CSV (공고번호, 업무) — --fill-notice면 필요 없음")
    parser.add_argument("--out", default=str(ROOT / "output" / "bid_results.jsonl"), help="결과 파일(JSONL, 이어 쓰기)")
    parser.add_argument("--limit", type=int, default=0, help="이번에 받을 최대 공고 수(시험용, 0이면 전부)")
    parser.add_argument("--sleep", type=float, default=0.3, help="공고 사이 쉬는 초(기본 0.3)")
    parser.add_argument("--fill-notice", action="store_true",
                        help="이미 받은 결과 파일에 공고 정보(하한율·변동폭·A값·개찰일)만 채움 — 개찰 결과는 다시 안 받음")
    parser.add_argument("--fill-a", action="store_true",
                        help="이미 받은 결과 파일에서 A값이 빈 줄만 기초금액 정보를 다시 찾아봄(하한선을 가늠하지 않고 정확히 계산하려고)")
    args = parser.parse_args(argv)
    if args.fill_a:
        args.fill_notice = True
    if not args.fill_notice and not args.targets:
        parser.error("공고번호 목록 CSV를 주거나 --fill-notice를 쓰세요")

    key = service_key()
    if not key:
        print("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.", file=sys.stderr)
        return 1
    out_path = Path(args.out)
    if args.fill_notice:
        if not out_path.exists():
            print(f"결과 파일이 없습니다: {out_path}", file=sys.stderr)
            return 1
        client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=30.0, max_retries=3))
        client.config.num_of_rows = 999
        filled, tried, stopped = fill_notices(client, out_path, args.limit, args.sleep, only_a=args.fill_a)
        print(f"끝 — 찾아본 {tried}건 중 {'A값' if args.fill_a else '하한율'} 찾음 {filled}건. 결과 파일: {out_path}")
        if not stopped and tried > filled:
            print("하한율을 못 찾은 공고는 다시 실행해도 건너뜁니다(이미 찾아봄 표시).")
        return 0
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
                rec = fetch_one(client, base_url, notice_input, work)
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
