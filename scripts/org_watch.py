"""기관별 따로 수집기 — 나라장터에서 정한 기관(기본 한전KDN) 공고만 받아 `config/org_watch.json`의 낱말 묶음으로 거름(2026-11-24 사용자 요청).
웹앱 수집 규칙과 따로 돎(웹앱 화면·텔레그램·판단 기록은 안 건드림).

서버 PC에서(나라장터 인증키가 있는 곳):

    py scripts/org_watch.py                  # 최근 7일, 한전KDN
    py scripts/org_watch.py --days 3 --prespec   # 사전규격도
    py scripts/org_watch.py --all            # 그 기관 공고 전부(걸린 것·빠진 것 모두) CSV에

- 나라장터 본공고 3종(공사·용역·물품)을 받아(`pipeline.collect_notices` — 7일씩 나눠 받음) 최신 차수만, 취소공고 뺌,
  공고기관·수요기관 이름에 기관 이름이 든 공고 → 낱말 묶음(전시·행사 / 홍보관·공간 / 영상·콘텐츠·홍보물 / 통신공사(공사만)) → 제외 낱말이면 뺌.
  한전KDN 자체 번호(P2026…·2026…-01) 공고도 나라장터 API로 들어옴(2026-11-24 서버 PC에서 확인).
- **새로 나온 공고**: 지난 실행에서 못 본 공고(공고번호-차수)를 맨 위에. 본 기록 `data/org_watch_seen.json`.
- 출력: 화면 요약 + `output/기관수집_<기관>_날짜.csv`. 한전KDN 공고는 당일·익일 마감이 많아 하루 여러 번 돌리는 것이 좋음.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nego import scope  # noqa: E402
from nego.org_watch import OrgRule, load_rules  # noqa: E402
from nego.screen import build_schedule  # noqa: E402

STATE = ROOT / "data" / "org_watch_seen.json"


def load_env_key() -> None:
    if os.environ.get("NARA_SERVICE_KEY", "").strip():
        return
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("NARA_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip().strip("'\"")
                os.environ["NARA_SERVICE_KEY"] = urllib.parse.unquote(key) if "%" in key else key


def load_seen(path: Path) -> dict[str, dict[str, str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_seen(path: Path, seen: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")


def rows_for(rule: OrgRule, notices, now: datetime, seen: dict[str, str]) -> list[dict]:
    """그 기관 공고마다 한 줄 — 판정 = 대상 / 제외 낱말 / 묶음 없음. seen(공고키 → 처음 본 시각)에 없으면 새 공고."""
    rows = []
    for n in scope.latest_ordinals(notices):
        if scope.is_cancelled(n) or not rule.owns(n):
            continue
        hits, excluded = rule.match(n)
        verdict = "대상" if hits and not excluded else "제외 낱말" if hits else "묶음 없음"
        sched = build_schedule(n)
        key = f"{n.notice_no}-{n.notice_ord}"
        rows.append({
            "판정": verdict,
            "새 공고": "새" if verdict == "대상" and key not in seen else "",
            "묶음": " · ".join(hits),
            "낱말": ", ".join(w for ws in hits.values() for w in ws) + (f" (제외: {', '.join(excluded)})" if excluded else ""),
            "공고번호": key,
            "공고명": n.title,
            "업무": n.work_type,
            "구분": n.kind or "",
            "유형": scope.bid_category(n) or "",
            "계약방법": n.contract_method or "",
            "금액": int(n.estimated_price) if n.estimated_price else "",
            "게시": (n.posted_at or "")[:16],
            "마감": sched.earliest[1].strftime("%Y-%m-%d %H:%M") if sched.earliest else "",
            "마감 지남": "예" if sched.is_expired(now) else "",
            "공고기관": n.notice_institution or "",
            "수요기관": n.demand_institution or "",
            "공고 주소": n.detail_url or "",
        })
    order = {"대상": 0, "제외 낱말": 1, "묶음 없음": 2}
    rows.sort(key=lambda r: (order[r["판정"]], r["새 공고"] != "새", r["마감 지남"] == "예", r["마감"] or "9999"))
    return rows


def summary(org: str, rows: list[dict], begin: datetime, now: datetime) -> str:
    target = [r for r in rows if r["판정"] == "대상"]
    new = [r for r in target if r["새 공고"]]
    lines = [f"{org} 공고 따로 수집 — 게시 {begin:%Y-%m-%d} ~ {now:%Y-%m-%d %H:%M}",
             f"그 기관 공고 {len(rows)}건 · 대상 {len(target)}건(새 공고 {len(new)}) · 제외 낱말 {sum(r['판정'] == '제외 낱말' for r in rows)}건"]

    def block(title: str, items: list[dict]) -> None:
        if not items:
            return
        lines.extend(["", title])
        for r in items:
            money = f"{r['금액']:,}원" if r["금액"] != "" else "금액 없음"
            late = " · 마감 지남" if r["마감 지남"] else ""
            lines.append(f"  [{r['묶음']}] {r['업무']} · {r['유형'] or '-'} · 마감 {r['마감'] or '-'}{late} · {money}")
            lines.append(f"      {r['공고명']} ({r['공고번호']})")

    block("새로 나온 공고:", new)
    block("이미 본 공고(마감 전):", [r for r in target if not r["새 공고"] and not r["마감 지남"]])
    return "\n".join(lines)


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        cols = list(rows[0]) if rows else ["판정", "공고번호", "공고명"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def collect(days: int, prespec: bool, now: datetime):
    from nego.config import load_config
    from nego.http_client import DataGoKrClient
    from nego.pipeline import RunStats, collect_notices, collect_prespecs, window_begin

    config = load_config()
    if not config.api.service_key:
        raise SystemExit("나라장터 인증키가 없습니다 — NARA_SERVICE_KEY 환경변수나 레포 루트 .env를 확인하세요.")
    client, stats = DataGoKrClient(config.api), RunStats()
    begin = window_begin(now, days)
    b, e = begin.strftime("%Y%m%d%H%M"), now.strftime("%Y%m%d%H%M")
    notices = collect_notices(client, b, e, stats)
    if prespec:
        notices += collect_prespecs(client, b, e, stats)
    return notices, begin


def main(argv: list[str] | None = None, notices=None) -> int:
    p = argparse.ArgumentParser(description="나라장터에서 정한 기관 공고만 받아 따로 낱말 묶음으로 거름(기본 한전KDN)")
    p.add_argument("--org", action="append", help="config/org_watch.json의 기관 이름(여러 번 가능, 기본 전부)")
    p.add_argument("--days", type=int, default=7, help="오늘 포함 최근 며칠 게시 공고(기본 7)")
    p.add_argument("--prespec", action="store_true", help="사전규격도 받음")
    p.add_argument("--all", action="store_true", help="CSV에 그 기관 공고 전부(빠진 것도)")
    p.add_argument("--out", type=Path, default=ROOT / "output", help="결과 폴더(기본 output)")
    p.add_argument("--state", type=Path, default=STATE, help="본 공고 기록 파일")
    args = p.parse_args(argv)

    rules = load_rules()
    orgs = args.org or list(rules)
    unknown = [o for o in orgs if o not in rules]
    if unknown:
        print(f"config/org_watch.json에 없는 기관: {', '.join(unknown)}", file=sys.stderr)
        return 2
    now = datetime.now()
    if notices is None:
        load_env_key()
        print(f"나라장터에서 최근 {args.days}일 공고를 받는 중(몇 분 걸림)…", file=sys.stderr)
        notices, begin = collect(args.days, args.prespec, now)
    else:
        begin = now - timedelta(days=args.days - 1)

    seen_all = load_seen(args.state)
    for org in orgs:
        seen = seen_all.setdefault(org, {})
        rows = rows_for(rules[org], notices, now, seen)
        print(summary(org, rows, begin, now))
        out = args.out / f"기관수집_{org}_{date.today().isoformat()}.csv"
        write_csv(rows if args.all else [r for r in rows if r["판정"] != "묶음 없음"], out)
        print(f"\nCSV: {out}\n")
        for r in rows:
            if r["판정"] == "대상":
                seen.setdefault(r["공고번호"], now.strftime("%Y-%m-%d %H:%M"))
    save_seen(args.state, seen_all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
