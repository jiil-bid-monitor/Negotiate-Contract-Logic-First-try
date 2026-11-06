"""과거 실적 폴더 이름 → 나라장터 공고를 찾아 제안요청서·과업지시서를 그 폴더에 받는다(2026-11-06 사용자 요청, 서버 PC에서).

과거 실적(싱크로율 비교용, 비공개 레포 jiil-past-contracts)에 사업을 더할 때, 빈 사업 폴더만 만들어 두면
폴더 이름으로 나라장터 공고를 찾아 첨부를 받아 넣는다.

    py -u scripts/fetch_past_docs.py "C:\\...\\과거실적폴더"          # 비어 있는 사업 폴더 전부
    py -u scripts/fetch_past_docs.py "C:\\...\\과거실적폴더" --dry-run # 찾기만(받지 않음)

- 폴더 이름: '2025_고한읍 둘레길 야간경관 조성사업'처럼 '연도_사업명' — 연도 앞 해 1월부터 그 해 말까지 공고명을 찾는다
  (공사·용역·물품, 공고명 낱말 검색). 끝의 '_241213' 같은 날짜는 뺀다.
- 공고명에 폴더 이름 낱말이 가장 많이 든 공고 하나를 고른다. 비슷한 공고가 여럿이거나 낱말이 절반도 안 맞으면 받지 않고
  `_찾은_공고_후보.txt`에 후보만 적는다(엉뚱한 사업 문서가 섞이지 않게) — 맞는 공고번호를 골라 `--pick 폴더이름=공고번호`로 다시.
- 받는 파일: 첨부 중 이름에 제안요청·과업(지시·내용)이 든 것만(공고문·시방서 등은 안 받음 — 없으면 0개, `_공고정보.txt`에 첨부 목록). 받은 공고는 폴더 안 `_공고정보.txt`에 적는다.
- 파일이 이미 든 폴더는 건너뛴다. 인증키는 이 레포 .env(웹앱과 같은 키). 하루 호출 한도에 걸리면 멈춘다 — 다음 날 다시.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import fields as F  # noqa: E402
from nego.http_client import ApiConfig, ApiResultError, DataGoKrClient  # noqa: E402
from quantdocs.g2b import fetch_notice, service_key  # noqa: E402

OPS = {"공사": "getBidPblancListInfoCnstwkPPSSrch", "용역": "getBidPblancListInfoServcPPSSrch",
       "물품": "getBidPblancListInfoThngPPSSrch"}
QUOTA = "22"
# 검색어로는 안 쓰는 흔한 낱말(점수 계산엔 씀)
GENERIC = set("사업 제작 설치 제작설치 공사 리모델링 조성 조성사업 구축 전시물 및 시설 센터 교육 체험공간 디자인 용역 설계 "
              "실시설계 기본 정비 개선 일원 사업명".split())
KEEP = re.compile(r"제안\s*요청|과업\s*(?:지시|내용)|과업서")

SKIP_FILES = {".gitkeep", "_공고정보.txt", "_찾은_공고_후보.txt", "desktop.ini", "Thumbs.db"}


def _sq(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def parse_folder(name: str) -> tuple[int | None, str, list[str]]:
    """(연도, 사업명, 낱말들)."""
    m = re.match(r"(\d{4})[_\s-]+(.*)", name)
    year, rest = (int(m.group(1)), m.group(2)) if m else (None, name)
    rest = re.sub(r"[_\s]+\d{6}$", "", rest)            # 끝 날짜 '_241213'
    rest = re.sub(r"\([^)]*\)", " ", rest)              # '(제작설치)'
    words = [w for w in re.split(r"[_\s·,]+", rest) if len(w) >= 2]
    return year, rest.replace("_", " ").strip(), words


def search_terms(words: list[str]) -> list[str]:
    """공고명 검색어 — 흔하지 않은 낱말 중 긴 것 2개, 숫자로 시작하면 숫자 뺀 말도('365세이프타운' → '세이프타운')."""
    terms = []
    for w in sorted((w for w in words if w not in GENERIC), key=len, reverse=True)[:2]:
        terms.append(w)
        stripped = re.sub(r"^\d+", "", w)
        if stripped != w and len(stripped) >= 2:
            terms.append(stripped)
    return terms or words[:1]


def score(title: str, words: list[str]) -> float:
    t = _sq(title)
    hits = sum(1 for w in words if _sq(w) in t or re.sub(r"^\d+", "", w) in t and len(re.sub(r"^\d+", "", w)) >= 2)
    return hits / max(1, len(words))


def months(y0: int, y1: int):
    today = date.today()
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            if date(y, m, 1) > today:
                return
            end = 28 if m == 2 else 30 if m in (4, 6, 9, 11) else 31
            yield f"{y}{m:02d}010000", f"{y}{m:02d}{end}2359"


def find(client: DataGoKrClient, year: int | None, words: list[str]) -> list[dict]:
    y1 = year or date.today().year
    seen: dict[str, dict] = {}
    for term in search_terms(words):
        for work, op in OPS.items():
            rows = []  # 한 번에 긴 기간은 '조회기간이 너무 깁니다'(resultCode=07) — 처음부터 달마다
            for b, e in months(y1 - 1, y1):
                try:
                    rows += client.fetch_all_pages(F.BID_NOTICE_BASE_URL, op, {"inqryDiv": "1", "inqryBgnDt": b,
                                                   "inqryEndDt": e, "bidNtceNm": term}, f"찾기 {term}")
                except ApiResultError as err:
                    if err.result_code == QUOTA:
                        raise
            for r in rows:
                no = str(r.get("bidNtceNo") or "")
                if not no:
                    continue
                cur = {"no": no, "ord": str(r.get("bidNtceOrd") or ""), "title": str(r.get("bidNtceNm") or ""),
                       "inst": str(r.get("dminsttNm") or r.get("ntceInsttNm") or ""), "posted": str(r.get("bidNtceDt") or "")[:10],
                       "work": work}
                cur["score"] = score(cur["title"], words)
                old = seen.get(no)
                if old is None or cur["ord"] > old["ord"]:
                    seen[no] = cur
    return sorted(seen.values(), key=lambda c: (-c["score"], c["posted"]), reverse=False)


def choose(cands: list[dict]) -> dict | None:
    """가장 잘 맞는 공고 — 점수가 절반 이상이고, 같은 점수의 다른 공고는 같은 사업의 재공고(공고명 같음)일 때만. 재공고면 최근 것."""
    if not cands or cands[0]["score"] < 0.5:
        return None
    top = [c for c in cands if c["score"] == cands[0]["score"]]
    if len({_sq(c["title"]) for c in top}) > 1:
        return None
    return max(top, key=lambda c: c["posted"])


def download(cand: dict, folder: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        nf = fetch_notice(cand["no"], Path(tmp))
        files = list(nf.files)
        keep = [f for f in files if KEEP.search(f.name)]  # 제안요청서·과업지시서만(11/06 사용자 결정 — 공고문·시방서 등은 안 받음)
        got = []
        for f in keep:
            shutil.copy2(f, folder / f.name)
            got.append(f.name)
    (folder / "_공고정보.txt").write_text(
        f"공고번호: {cand['no']}-{cand['ord']}\n공고명: {cand['title']}\n수요기관: {cand['inst']}\n게시일: {cand['posted']}\n"
        f"받은 파일: {', '.join(got) or '(제안요청서·과업지시서 이름의 첨부 없음)'}\n"
        f"첨부 전체: {', '.join(f.name for f in files)}\n", encoding="utf-8")
    return got


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="과거 실적 폴더 이름으로 나라장터 제안요청서·과업지시서 받기")
    ap.add_argument("root", help="사업 폴더들이 든 폴더")
    ap.add_argument("--dry-run", action="store_true", help="찾기만 하고 받지 않음")
    ap.add_argument("--pick", action="append", default=[], help="폴더이름=공고번호 — 후보 중 직접 고른 공고로 받기")
    args = ap.parse_args(argv)
    key = service_key()
    if not key:
        print("나라장터 인증키가 없습니다 — 이 레포 .env의 NARA_SERVICE_KEY를 확인하세요", file=sys.stderr)
        return 1
    client = DataGoKrClient(ApiConfig(service_key=key, timeout_sec=30.0, max_retries=2, max_pages=10))
    client.config.num_of_rows = 999
    picks = dict(p.split("=", 1) for p in args.pick if "=" in p)
    root = Path(args.root)
    folders = sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith("."))
    done = skipped = unsure = 0
    for d in folders:
        if any(f.name not in SKIP_FILES for f in d.iterdir()):
            print(f"· {d.name}: 이미 파일이 있어 건너뜀")
            skipped += 1
            continue
        year, title, words = parse_folder(d.name)
        try:
            if d.name in picks:
                cand = {"no": picks[d.name].split("-")[0], "ord": "", "title": title, "inst": "", "posted": "", "work": "", "score": 1}
                cands = [cand]
            else:
                cands = find(client, year, words)
                cand = choose(cands)
        except ApiResultError as err:
            if err.result_code == QUOTA:
                print("\n오늘 나라장터 호출 한도에 걸려 멈춥니다 — 내일 같은 명령을 다시 실행하세요(받은 폴더는 건너뜀).")
                break
            print(f"! {d.name}: 조회 오류 {err}")
            continue
        if cand is None:
            unsure += 1
            lines = [f"{c['score']:.0%}  {c['no']}-{c['ord']}  {c['posted']}  [{c['work']}] {c['title']}  ({c['inst']})" for c in cands[:8]]
            (d / "_찾은_공고_후보.txt").write_text(
                f"검색: {title} → 확실한 공고를 못 골라 받지 않음. 맞는 공고번호를 골라 --pick \"{d.name}=공고번호\"로 다시 실행.\n\n"
                + ("\n".join(lines) or "(후보 없음 — 나라장터에 없는 사업이거나 공고명이 폴더 이름과 많이 다름)") + "\n", encoding="utf-8")
            print(f"? {d.name}: 확실한 공고 없음 — 후보 {len(cands)}건을 폴더의 _찾은_공고_후보.txt에")
            continue
        if args.dry_run:
            print(f"○ {d.name}: {cand['no']}-{cand['ord']} {cand['title']} ({cand['posted']}) — 받지 않음(--dry-run)")
            continue
        try:
            got = download(cand, d)
        except Exception as err:  # noqa: BLE001
            print(f"! {d.name}: 받기 실패 {err}")
            continue
        done += 1
        print(f"✓ {d.name}: {cand['no']} {cand['title']} → {len(got)}개 ({', '.join(got) or '해당 첨부 없음 — _공고정보.txt 확인'})")
    print(f"\n끝 — 받음 {done} · 확실한 공고 없음 {unsure} · 이미 있어 건너뜀 {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
