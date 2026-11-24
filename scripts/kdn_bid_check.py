"""나라장터에서 한전KDN(또는 다른 기관) 공고만 골라 지금 수집 규칙의 판정을 보여 주는 도구(2026-11-24 사용자 요청 —
한전 전력데이터 개방포털이 회사망에서 막혀, 나라장터에 올라오는 한전KDN 공고부터 지일 관련 공고를 모아 봄).

서버 PC에서(나라장터 인증키가 있는 곳):

    py scripts/kdn_bid_check.py                    # 최근 30일 본공고 중 한전KDN 공고
    py scripts/kdn_bid_check.py --days 60 --prespec  # 기간 늘리기 + 사전규격도
    py scripts/kdn_bid_check.py --org 한국전력공사 --org 한국수력원자력   # 다른 기관(공고기관·수요기관 이름에 든 글자)

- 웹앱 수집과 같은 흐름(`nego.pipeline.run` — 본공고 3종·면허제한·참가가능지역을 받아 `build_candidates`)으로 판정한 뒤
  공고기관·수요기관 이름에 기관 이름이 든 공고만 남김(띄어쓰기·대소문자 무시, 한전KDN은 '한전케이디엔'·'KEPCO KDN'도).
  그래서 판정(관심·제외 키워드, '통신'은 공사만, 보유 면허 경로, 보유면허없음 등)은 웹앱 실제 공고 탭과 같음. 마감 지난 공고도 표시만 하고 남김.
- 출력: 화면 요약(후보 → 키워드만 안 맞음(참가 가능) → 그 밖에 빠짐) + `output/기관공고점검_날짜.csv`.
- 나라장터 조회기간 한도(7일)는 수집 코드가 나눠 받음. 30일이면 본공고 3종을 다 받아 몇 분 걸림.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_ORGS = ("한전KDN",)
# 기관 이름의 다른 표기(띄어쓰기 뺀 소문자로 비교)
ALIASES = {"한전kdn": ("한전kdn", "한전케이디엔", "kepcokdn")}


def load_env_key() -> None:
    """나라장터 인증키를 환경변수 → 레포 루트 .env 순으로(웹앱 start.sh가 만든 파일)."""
    if os.environ.get("NARA_SERVICE_KEY", "").strip():
        return
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("NARA_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
                os.environ["NARA_SERVICE_KEY"] = urllib.parse.unquote(key) if "%" in key else key


def _squash(text: str | None) -> str:
    return re.sub(r"\s", "", text or "").lower()


def org_words(orgs: list[str]) -> list[str]:
    out = []
    for org in orgs:
        word = _squash(org)
        out += ALIASES.get(word, (word,))
    return out


def org_hit(notice, words: list[str]) -> str | None:
    """공고기관·수요기관 중 기관 이름이 든 쪽 이름(없으면 None)."""
    for name in (notice.notice_institution, notice.demand_institution):
        if name and any(w in _squash(name) for w in words):
            return name
    return None


def row_of(c, now: datetime) -> dict:
    n, s = c.notice, c.screen_result
    if c.is_candidate:
        verdict = "후보"
        route = ("보유 면허" if getattr(s, "license_match", None) else
                 "정선 사업" if getattr(s, "local_match", None) else "키워드·세부품명")
        why = ", ".join(s.matched_keywords or []) or ", ".join(getattr(s, "license_match", None) or []) or ""
        if s.confidence == "검토필요":
            why += f" (제외 키워드 '{s.review_exclude}'도 있음)"
    else:
        verdict = "키워드만 안 맞음" if c.only_keyword_miss else "빠짐"
        route, why = "", c.excluded_reason or ""
    deadline = c.schedule.earliest[1].strftime("%Y-%m-%d %H:%M") if c.schedule.earliest else ""
    return {
        "판정": verdict,
        "강도": s.confidence or "" if c.is_candidate else "",
        "들어온 길": route,
        "이유": why,
        "공고번호": f"{n.notice_no}-{n.notice_ord}",
        "공고명": n.title,
        "공고기관": n.notice_institution or "",
        "수요기관": n.demand_institution or "",
        "업무": n.work_type,
        "유형": c.category or "",
        "계약방법": n.contract_method or "",
        "금액": int(n.estimated_price) if n.estimated_price else "",
        "마감": deadline,
        "마감 지남": "예" if c.schedule.is_expired(now) else "",
        "자격": c.qualification.summary,
        "공고 주소": n.detail_url or "",
    }


ORDER = {"후보": 0, "키워드만 안 맞음": 1, "빠짐": 2}


def pick(candidates, rejected, words: list[str], now: datetime) -> list[dict]:
    rows, seen = [], set()
    for c in list(candidates) + list(rejected):
        key = (c.notice.notice_no, c.notice.notice_ord)
        if key in seen or not org_hit(c.notice, words):
            continue
        seen.add(key)
        rows.append(row_of(c, now))
    rows.sort(key=lambda r: (ORDER[r["판정"]], r["마감 지남"] == "예", r["마감"] or "9999"))
    return rows


def summary(rows: list[dict], orgs: list[str], begin: date, end: date) -> str:
    lines = [f"{'·'.join(orgs)} 나라장터 공고 점검 — 게시일 {begin} ~ {end}", f"그 기관 공고 {len(rows)}건"]
    verdicts = Counter(r["판정"] for r in rows)
    lines.append(" · ".join(f"{k} {verdicts.get(k, 0)}" for k in ORDER))
    lines.append("업무별: " + " · ".join(f"{k} {v}" for k, v in Counter(r["업무"] for r in rows).most_common()))
    words = Counter(r["이유"].split("(", 1)[1].rstrip(")") for r in rows
                    if r["판정"] == "빠짐" and r["이유"].startswith("제외키워드 ("))
    if words:
        lines.append("제외 키워드로 빠진 것: " + " · ".join(f"{k} {v}" for k, v in words.most_common(10)))
    for title, verdict in (("후보(웹앱 실제 공고 탭에 나오는 공고)", "후보"),
                           ("키워드만 안 맞음 — 면허·지역·마감은 통과, 참가는 할 수 있음", "키워드만 안 맞음")):
        picked = [r for r in rows if r["판정"] == verdict]
        if not picked:
            continue
        lines += ["", f"{title}:"]
        for r in picked:
            money = f"{r['금액']:,}원" if r["금액"] != "" else "금액 없음"
            late = " · 마감 지남" if r["마감 지남"] else ""
            head = f"[{r['강도'] or r['판정']}] " if verdict == "후보" else ""
            lines.append(f"  {head}{r['업무']} · {r['유형'] or '-'} · 마감 {r['마감'] or '-'}{late} · {money} · {r['자격']}")
            why = f"{r['들어온 길']}: {r['이유']}" if r["들어온 길"] else r["이유"]
            lines.append(f"      {r['공고명']} ({r['공고번호']})" + (f" — {why}" if why else ""))
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        cols = list(rows[0]) if rows else ["판정", "공고번호", "공고명"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="나라장터에서 한전KDN 등 한 기관 공고만 골라 지금 수집 규칙 판정을 보여 줌")
    p.add_argument("--org", action="append", help="기관 이름(공고기관·수요기관 이름에 든 글자, 여러 번 가능 — 기본 한전KDN)")
    p.add_argument("--days", type=int, default=30, help="오늘 포함 최근 며칠 게시 공고(기본 30)")
    p.add_argument("--prespec", action="store_true", help="사전규격도 같이 받음")
    p.add_argument("--out", type=Path, default=ROOT / "output", help="결과 폴더(기본 output)")
    args = p.parse_args(argv)

    load_env_key()
    from nego.config import load_config
    from nego.pipeline import run

    config = load_config()
    if not config.api.service_key:
        print("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.", file=sys.stderr)
        return 2
    orgs = args.org or list(DEFAULT_ORGS)
    now = datetime.now()
    begin = (now - timedelta(days=max(args.days, 1) - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"나라장터에서 {begin:%Y-%m-%d} ~ 지금 공고를 받는 중(몇 분 걸림)…", file=sys.stderr)
    candidates, stats, _ = run(config, now=now, include_prespec=args.prespec, period=(begin, now))
    if stats.license_error:
        print(f"⚠ 면허제한정보를 못 받음 — 보유 면허 경로·자격 판정이 빠짐: {stats.license_error}", file=sys.stderr)

    rows = pick(candidates, stats.rejected, org_words(orgs), now)
    print(summary(rows, orgs, begin.date(), now.date()))
    out_csv = args.out / f"기관공고점검_{date.today().isoformat()}.csv"
    write_csv(rows, out_csv)
    print(f"\nCSV: {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
