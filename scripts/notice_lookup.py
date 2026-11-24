"""공고번호 목록을 나라장터에서 하나씩 조회해 규모(금액)·계약방법·일정·낙찰 결과를 표로 남기는 도구(2026-11-24 사용자 요청 —
한전KDN 6개월 공고 중 지일 관련 17건의 규모를 보려고. 나라장터 화면 엑셀엔 금액 칸이 없음).

서버 PC에서(나라장터 인증키가 있는 곳):

    py scripts/notice_lookup.py docs/kdn_targets_2026-05_11.csv
    py scripts/notice_lookup.py R26BK01839771-000 P2026310-01      # 번호를 바로 적어도 됨

- 목록 CSV는 '공고번호' 칸(있으면 '업무' 칸도 — 공사·용역·물품, 없으면 세 업무를 차례로 찾음)을 읽음. 공고번호 뒤 차수('-000'·'-01')는 떼고 찾고 마지막 차수를 씀.
- 공고마다: 입찰공고 조회(inqryDiv=2 + bidNtceNo — 투찰 계산기와 같은 방식) → 추정가격·배정예산·계약방법·낙찰방법·게시·마감·개찰·주소,
  낙찰현황(inqryDiv=4 + bidNtceNo — 낙찰 후 서류와 같은 방식) → 낙찰 업체·금액·낙찰률(개찰 전·유찰이면 빈칸).
- 출력: 화면 표 + `output/공고조회_날짜.csv`, 묶음(목록 CSV의 '묶음' 칸)이 있으면 묶음별 건수·금액 범위도. 낙찰정보 서비스가 안 되면 낙찰 칸만 비움.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import urllib.parse
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKS = ("공사", "용역", "물품")


def load_env_key() -> None:
    if os.environ.get("NARA_SERVICE_KEY", "").strip():
        return
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("NARA_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
                os.environ["NARA_SERVICE_KEY"] = urllib.parse.unquote(key) if "%" in key else key


def read_targets(args: list[str]) -> list[dict]:
    """CSV 파일(공고번호·업무·묶음·공고명 칸) 또는 공고번호들 → [{no, work, group, title}]."""
    out = []
    for arg in args:
        path = Path(arg)
        if path.suffix.lower() == ".csv" and path.exists():
            with path.open(encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    if (r.get("공고번호") or "").strip():
                        out.append({"no": r["공고번호"].strip(), "work": (r.get("업무") or "").strip(),
                                    "group": (r.get("묶음") or "").strip(), "title": (r.get("공고명") or "").strip()})
        elif arg.strip():
            out.append({"no": arg.strip(), "work": "", "group": "", "title": ""})
    return out


def split_no(text: str) -> tuple[str, str]:
    no, _, ord_ = text.strip().upper().partition("-")
    return no, ord_


def _num(value) -> float | None:
    try:
        text = str(value).replace(",", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def _latest(rows: list[dict], no: str) -> dict | None:
    same = [r for r in rows if str(r.get("bidNtceNo") or "").strip().upper() == no] or rows
    return max(same, key=lambda r: str(r.get("bidNtceOrd") or "")) if same else None


def find_notice(client, no: str, work: str) -> tuple[str, dict | None, str]:
    """(업무, 공고 행, 오류). 업무를 모르면 공사·용역·물품 순."""
    from nego import fields as F
    from nego.http_client import ApiError

    errors = []
    for w in ([work] if work in WORKS else list(WORKS)) + [x for x in WORKS if work in WORKS and x != work]:
        try:
            rows = client.fetch_all_pages(F.BID_NOTICE_BASE_URL, F.BID_NOTICE_OPERATIONS[w],
                                          {"inqryDiv": "2", "bidNtceNo": no}, f"공고/{w} {no}")
        except ApiError as err:
            errors.append(f"{w}: {err}")
            continue
        row = _latest(rows, no)
        if row:
            return w, row, ""
    return work, None, "; ".join(errors) or "나라장터에서 찾지 못함"


def find_award(client, no: str, work: str) -> dict | None:
    from nego import fields as F
    from nego.http_client import ApiError

    base = os.environ.get("SCSBID_BASE_URL", "").strip() or F.SCSBID_BASE_URL
    try:
        rows = client.fetch_all_pages(base, F.SCSBID_OPERATIONS[f"낙찰현황/{work}"],
                                      {"inqryDiv": "4", "bidNtceNo": no}, f"낙찰현황/{work} {no}")
    except (ApiError, KeyError):
        return None
    return _latest(rows, no) if rows else None


def row_of(target: dict, work: str, notice: dict | None, award: dict | None, error: str) -> dict:
    n, a = notice or {}, award or {}
    est, budget = _num(n.get("presmptPrce")), _num(n.get("asignBdgtAmt"))
    amount = budget or (est * 1.1 if est else None)  # 사업금액 = 배정예산(없으면 추정가격 × 1.1 — 웹앱과 같은 어림)
    win = _num(a.get("sucsfbidAmt"))
    return {
        "묶음": target["group"],
        "공고번호": f"{split_no(target['no'])[0]}-{n.get('bidNtceOrd') or split_no(target['no'])[1]}",
        "공고명": n.get("bidNtceNm") or target["title"],
        "업무": work,
        "사업금액(원)": int(amount) if amount else "",
        "추정가격(원)": int(est) if est else "",
        "배정예산(원)": int(budget) if budget else "",
        "계약방법": n.get("cntrctCnclsMthdNm") or "",
        "낙찰방법": n.get("sucsfbidMthdNm") or "",
        "게시": str(n.get("bidNtceDt") or "")[:16],
        "마감": str(n.get("bidClseDt") or "")[:16],
        "개찰": str(n.get("opengDt") or "")[:16],
        "수요기관": n.get("dminsttNm") or "",
        "낙찰 업체": a.get("bidwinnrNm") or "",
        "낙찰 금액(원)": int(win) if win else "",
        "낙찰률(%)": a.get("sucsfbidRate") or "",
        "참가 수": a.get("prtcptCnum") or "",
        "공고 주소": n.get("bidNtceDtlUrl") or "",
        "조회 오류": error,
    }


def money(v) -> str:
    return f"{v / 1e8:.2f}억" if v and v >= 1e8 else f"{v / 1e4:,.0f}만" if v else "-"


def summary(rows: list[dict]) -> str:
    found = [r for r in rows if not r["조회 오류"]]
    lines = [f"조회 {len(rows)}건 · 찾음 {len(found)}건 · 못 찾음 {len(rows) - len(found)}건", ""]
    for r in rows:
        amt = r["낙찰 금액(원)"] or r["사업금액(원)"]
        tag = "낙찰" if r["낙찰 금액(원)"] else "사업금액"
        lines.append(f"  {r['묶음'] or '-'} | {r['업무'] or '-'} | {tag} {money(amt)} | {r['계약방법'] or '-'} | {r['공고명'][:40]}"
                     + (f"  ⚠ {r['조회 오류'][:60]}" if r["조회 오류"] else ""))
    groups: dict[str, list[float]] = defaultdict(list)
    for r in found:
        amt = r["낙찰 금액(원)"] or r["사업금액(원)"]
        groups[r["묶음"] or "(묶음 없음)"].append(float(amt) if amt else 0.0)
    if groups:
        lines += ["", "묶음별 (금액 = 낙찰 금액, 없으면 사업금액):"]
        for g, vals in groups.items():
            known = [v for v in vals if v]
            rng = f"{money(min(known))} ~ {money(max(known))} · 가운데 {money(statistics.median(known))}" if known else "금액 없음"
            lines.append(f"  {g}: {len(vals)}건 · {rng}")
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["공고번호"])
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None, client=None) -> int:
    p = argparse.ArgumentParser(description="공고번호 목록을 나라장터에서 조회해 금액·계약방법·낙찰 결과를 표로")
    p.add_argument("targets", nargs="+", help="목록 CSV(공고번호 칸) 또는 공고번호")
    p.add_argument("--no-award", action="store_true", help="낙찰 결과는 조회하지 않음")
    p.add_argument("--out", type=Path, default=ROOT / "output", help="결과 폴더(기본 output)")
    args = p.parse_args(argv)

    targets = read_targets(args.targets)
    if not targets:
        print("조회할 공고번호가 없습니다.", file=sys.stderr)
        return 2
    if client is None:
        load_env_key()
        from nego.config import load_config
        from nego.http_client import DataGoKrClient

        config = load_config()
        if not config.api.service_key:
            print("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.", file=sys.stderr)
            return 2
        import dataclasses
        client = DataGoKrClient(dataclasses.replace(config.api, max_retries=2, max_pages=3))

    rows = []
    for i, t in enumerate(targets, 1):
        no, _ = split_no(t["no"])
        print(f"  [{i}/{len(targets)}] {t['no']}", file=sys.stderr)
        work, notice, error = find_notice(client, no, t["work"])
        award = find_award(client, no, work) if notice and not args.no_award else None
        rows.append(row_of(t, work, notice, award, error))
    print(summary(rows))
    out = args.out / f"공고조회_{date.today().isoformat()}.csv"
    write_csv(rows, out)
    print(f"\nCSV: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
