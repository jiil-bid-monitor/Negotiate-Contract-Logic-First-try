"""투찰 이력(21라인 "경쟁업체참여이력" .xls) → fetch_bid_results.py 수집 목록 CSV.

투찰 이력은 `python scripts/bid_history_vault.py open`으로 푼 `data/bid_history/`(깃 밖)에서 읽고, 목록도 깃 밖 `data/`에 쓴다 —
공고번호 목록 = 지일 투찰 이력이라 저장소에 올리지 않는다.

    py scripts/make_bid_targets.py                 # 조달청 공고만 → data/bid_results_targets.csv
    py scripts/make_bid_targets.py --all           # 국방부·한전 등 자체 조달 공고까지(나라장터 API에 없을 가능성 큼)

CSV 칸: 공고번호, 업무(공사·물품·용역), 기관, 입력일 — fetch_bid_results.py는 앞 두 칸만 읽는다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

from lxml import html

ROOT = Path(__file__).resolve().parent.parent

# 공고번호 칸의 업무 이름 → 업무
WORKS = {
    "시설": "공사", "공사": "공사", "수의시설": "공사",
    "물품": "물품", "내자": "물품", "물품(내자)": "물품", "수의내자": "물품", "자재": "물품",
    "용역": "용역",
}
G2B = "조달청"  # 나라장터 공고(이력의 기관 이름)
NO_COL, DATE_COL = 2, 25  # 공고번호 칸("조달청 시설 R26BK01772578-000 호"), 입력일 칸


def parse_notice_cell(text: str) -> tuple[str, str, str]:
    """'조달청 시설 R26BK01772578-000 호' → ('조달청', '공사', 'R26BK01772578-000'). 업무 이름이 없으면 업무는 ''."""
    parts = text.replace("호", " ").split()
    if not parts:
        return "", "", ""
    agency = parts[0]
    work = WORKS.get(parts[1], "") if len(parts) > 2 else ""
    return agency, work, parts[-1]


def read_history(path: Path) -> list[dict]:
    """.xls(실제로는 HTML 표) 한 파일 → 공고 행 목록."""
    doc = html.fromstring(path.read_text(encoding="utf-8"))
    rows = []
    for tr in doc.iter("tr"):
        cells = [re.sub(r"\s+", " ", td.text_content()).strip() for td in tr.findall("td")]
        if len(cells) <= DATE_COL or not cells[0].isdigit():
            continue
        agency, work, no = parse_notice_cell(cells[NO_COL])
        rows.append({"no": no, "work": work, "agency": agency, "date": cells[DATE_COL]})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="투찰 이력 → 개찰결과·낙찰방법 수집 목록 CSV")
    parser.add_argument("--dir", default=str(ROOT / "data" / "bid_history"), help="투찰 이력 폴더(연도별 하위 폴더의 .xls)")
    parser.add_argument("--out", default=str(ROOT / "data" / "bid_results_targets.csv"), help="목록 CSV")
    parser.add_argument("--all", action="store_true", help="조달청 밖 기관(국방부·한전 등) 공고도 넣기")
    args = parser.parse_args(argv)

    files = sorted(Path(args.dir).glob("*/*.xls"))
    if not files:
        print(f"투찰 이력 파일이 없습니다: {args.dir} — 먼저 python scripts/bid_history_vault.py open", file=sys.stderr)
        return 1
    rows = [r for f in files for r in read_history(f)]
    seen: set[str] = set()
    targets = []
    for r in rows:
        if r["no"] in seen or (not args.all and r["agency"] != G2B):
            continue
        seen.add(r["no"])
        targets.append(r)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["공고번호", "업무", "기관", "입력일"])
        for r in targets:
            w.writerow([r["no"], r["work"], r["agency"], r["date"]])

    agencies = Counter(r["agency"] for r in rows)
    print(f"투찰 이력 {len(files)}개 파일 · {len(rows)}건 (기관: " + ", ".join(f"{k} {v}" for k, v in agencies.most_common()) + ")")
    print(f"목록 {len(targets)}건 → {out}  (업무: " + ", ".join(f"{k or '모름'} {v}" for k, v in Counter(r['work'] for r in targets).most_common()) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
