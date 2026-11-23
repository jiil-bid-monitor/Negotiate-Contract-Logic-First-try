"""판단 기록의 공고마다 제안요청서·과업지시서를 받아 과업 성격을 정리한다(2026-11-23 사용자 요청 — '과업 성격 다름'으로 불참한 공고의
과업이 실제로 무엇인지 파악해 수집 규칙 보정에 쓰려고).

서버 PC에서(나라장터 인증키가 있는 곳):

    py scripts/task_nature_scan.py "판단기록_2026-11-23.csv"
    py scripts/task_nature_scan.py "판단기록_2026-11-23.csv" --reason "과업 성격 다름" --with-join

- 입력: 웹앱 판단 기록 탭 'CSV 내려받기' 파일. 주된 사유에 `--reason`이 든 공고(기본 '과업 성격 다름'), `--with-join`이면 참가 공고도(비교용).
- 공고마다 첨부를 받아(`quantdocs.g2b.fetch_notice`) 과업 문서 순서(과업지시서 > 제안요청서 > 규격·시방 > 나머지, `attachment_sources.spec_rank`)로 읽고
  ① 과업 범위·내용·납품 품목 절 발췌 ② 과업 성격 낱말 수(제작·설치 / 납품·구매 / 유지보수·운영 / 설계 / 전시·콘텐츠) ③ 과업 태그(싱크로율과 같은
  jiil `task_profile.build_profile`, 없으면 빈칸)와 싱크로율 상위 과거 실적.
- 출력: `output/task_nature_날짜.csv`(공고마다 한 줄)·`.md`(사람이 읽는 정리) — 판단·회사 정보가 들어 있어 **공개 저장소에 올리지 말 것**.
  받은 첨부는 임시 폴더에 두었다가 지움.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 과업 성격 낱말 — 띄어쓰기 무시하고 과업 문서 글에서 센다. 무엇을 하는 일인지 가늠용(판정에는 안 씀)
NATURE_WORDS: dict[str, tuple[str, ...]] = {
    "제작·설치·시공": ("제작", "설치", "시공", "공사", "조성", "연출"),
    "납품·구매": ("구매", "구입", "납품", "공급", "물품", "모델명", "제조사", "규격서", "관급자재"),
    "유지보수·운영": ("유지보수", "유지관리", "점검", "운영", "상주", "장애처리", "정기점검"),
    "설계": ("실시설계", "기본설계", "설계도서", "설계용역"),
    "전시·콘텐츠": ("전시", "콘텐츠", "영상", "체험", "디자인", "미디어"),
}
# 과업 내용이 적힌 절의 제목 — 그 아래 글을 발췌
_SECTION_HEAD = re.compile(
    r"(?:과업|사업|용역)\s*(?:의\s*)?(?:내용|범위|개요|목적|세부\s*내용)|주요\s*과업|세부\s*과업|과업\s*수행\s*내용"
    r"|(?:구매|납품|설치)\s*(?:품목|내역|규격|목록)|제\s*작\s*내\s*용|공\s*사\s*개\s*요")
SECTION_CHARS = 700


def load_rows(path: Path, reason: str, with_join: bool) -> list[dict]:
    """판단 기록 CSV → 볼 공고 줄(공고번호 기준 한 번씩). 주된 사유에 reason이 든 불참 + with_join이면 참가."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    out, seen = [], set()
    for r in rows:
        no = (r.get("공고번호") or "").strip()
        pick = (r.get("판단") == "불참" and reason in (r.get("주된 사유") or "")) or (with_join and r.get("판단") == "참가")
        if no and pick and no not in seen:
            seen.add(no)
            out.append(r)
    return out


def nature_counts(text: str) -> dict[str, int]:
    flat = re.sub(r"\s+", "", text or "")
    return {k: sum(flat.count(w) for w in words) for k, words in NATURE_WORDS.items()}


def main_nature(counts: dict[str, int]) -> str:
    """가장 많이 나온 과업 성격(둘째와 1.5배 넘게 차이 나면 하나, 아니면 둘). 낱말이 없으면 빈칸."""
    ranked = sorted(((v, k) for k, v in counts.items() if v), reverse=True)
    if not ranked:
        return ""
    if len(ranked) == 1 or ranked[0][0] >= ranked[1][0] * 1.5:
        return ranked[0][1]
    return f"{ranked[0][1]} + {ranked[1][1]}"


def sections(text: str, limit: int = 3) -> list[str]:
    """과업 범위·내용 절 발췌 — 절 제목 줄부터 SECTION_CHARS자(목차 줄처럼 바로 다음 제목이 오는 짧은 줄은 건너뜀)."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out: list[str] = []
    for i, ln in enumerate(lines):
        if len(out) >= limit:
            break
        if not ln or len(ln) > 60 or not _SECTION_HEAD.search(ln):
            continue
        body = " ".join(x for x in lines[i + 1:i + 40] if x)
        if len(body) < 40 or (_SECTION_HEAD.search(body[:60]) and len(body) < 120) or re.match(r"\S*\s*(?:과업지시서|제안요청서).{0,10}참조", body):
            continue  # 목차 줄·'과업지시서 및 제안요청서 참조'만 있는 칸
        piece = re.sub(r"\s+", " ", f"{ln} — {body}")[:SECTION_CHARS]
        if not any(piece[:80] == o[:80] for o in out):
            out.append(piece)
    return out


def task_docs(files: list[Path]) -> list[Path]:
    """과업 문서 순서대로(과업지시서 > 제안요청서 > 규격·시방). 과업 문서가 하나라도 있으면 그것만 — 공고문의 '사업내용 | 과업지시서 참조'나
    입찰 절차 글이 과업 성격 낱말·발췌에 섞이지 않게. 없으면 전부(공고문 등)."""
    from nego.attachment_sources import is_spec_name, spec_rank

    ranked = sorted(files, key=lambda p: spec_rank(p.name))
    spec = [p for p in ranked if is_spec_name(p.name)]
    return spec or ranked


def read_text(paths: list[Path], max_chars: int = 200_000) -> tuple[str, list[str]]:
    """과업 문서 글(앞 문서부터, max_chars까지)과 읽은 파일 이름."""
    from quantdocs.notice import doc_text

    parts, used, total = [], [], 0
    for p in paths:
        try:
            t = doc_text(p)
        except Exception as err:  # noqa: BLE001 — 파일 하나 못 읽어도 계속
            print(f"  ! {p.name} 못 읽음: {err}")
            continue
        if not t.strip():
            continue
        parts.append(f"=== {p.name} ===\n{t}")
        used.append(p.name)
        total += len(t)
        if total >= max_chars:
            break
    return "\n".join(parts), used


def scan(rows: list[dict], work: Path) -> list[dict]:
    from nego.sync import PastIndex
    from quantdocs import g2b

    past = PastIndex.load()
    if not past.available:
        print(f"(과업 태그·싱크로율 없이 진행 — {past.error})")
    out = []
    for k, r in enumerate(rows, 1):
        no = r["공고번호"].strip()
        print(f"[{k}/{len(rows)}] {no} {r.get('공고명', '')[:40]}")
        folder = work / no
        info = {"판단": r.get("판단"), "주된 사유": r.get("주된 사유"), "공고번호": no, "공고명": r.get("공고명"), "업무": r.get("업무"),
                "수요기관": r.get("수요기관"), "수집 경로": r.get("수집 경로"), "읽은 문서": "", "과업 성격": "", "낱말 수": "",
                "과업 태그": "", "닮은 과거 실적": "", "과업 내용 발췌": "", "메모": ""}
        try:
            got = g2b.fetch_notice(no, folder, work=r.get("업무") or "", licenses=False)
        except Exception as err:  # noqa: BLE001
            info["메모"] = f"공고 조회 실패: {err}"
            out.append(info)
            continue
        if not got.title:
            info["메모"] = "나라장터에서 공고를 못 찾음"
            out.append(info)
            continue
        text, used = read_text(task_docs(got.files))
        info["읽은 문서"] = " · ".join(used) or "(읽은 첨부 없음)"
        if got.skipped:
            info["메모"] = "못 받은 첨부: " + "; ".join(got.skipped)[:200]
        counts = nature_counts(text)
        info["과업 성격"] = main_nature(counts)
        info["낱말 수"] = " · ".join(f"{a} {b}" for a, b in counts.items() if b)
        info["과업 내용 발췌"] = "\n".join(sections(text))
        if past.available and text.strip():
            s = past.score(got.title, text, top=2)
            # tags = {칸: [태그…]}(시설·주제·과업 등) — '칸: 태그, 태그' 로
            info["과업 태그"] = " / ".join(f"{f}: {', '.join(ts[:6])}" for f, ts in (s.get("tags") or {}).items() if ts)
            info["닮은 과거 실적"] = " / ".join(f"{t.get('title')} {round((t.get('score') or 0) * 100)}%" for t in s.get("top") or [])
        out.append(info)
        shutil.rmtree(folder, ignore_errors=True)
    return out


def write(out: list[dict], stem: Path) -> tuple[Path, Path]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    csv_path, md_path = stem.with_suffix(".csv"), stem.with_suffix(".md")
    if out:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0]))
            w.writeheader()
            w.writerows(out)
    lines = [f"# 과업 성격 정리 ({date.today():%Y-%m-%d}, {len(out)}건)", ""]
    by: dict[str, list[dict]] = {}
    for r in out:
        by.setdefault(r["과업 성격"] or "(낱말 없음·못 읽음)", []).append(r)
    lines.append("## 과업 성격별 건수")
    lines += [f"- {k}: {len(v)}건 (참가 {sum(1 for x in v if x['판단'] == '참가')})" for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))]
    for r in out:
        lines += ["", f"## [{r['판단']}] {r['공고명']} ({r['공고번호']})",
                  f"- 업무·기관·경로: {r['업무']} · {r['수요기관']} · {r['수집 경로'] or '-'}",
                  f"- 과업 성격: {r['과업 성격'] or '-'} ({r['낱말 수'] or '낱말 없음'})",
                  f"- 읽은 문서: {r['읽은 문서'] or '-'}"]
        if r["과업 태그"]:
            lines.append(f"- 과업 태그: {r['과업 태그']}")
        if r["닮은 과거 실적"]:
            lines.append(f"- 닮은 과거 실적: {r['닮은 과거 실적']}")
        if r["메모"]:
            lines.append(f"- 메모: {r['메모']}")
        for piece in (r["과업 내용 발췌"] or "").split("\n"):
            if piece:
                lines.append(f"  > {piece}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return csv_path, md_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="판단 기록 공고의 제안요청서·과업지시서로 과업 성격 정리")
    ap.add_argument("csv", type=Path, help="판단 기록 CSV(웹앱 판단 기록 탭 'CSV 내려받기')")
    ap.add_argument("--reason", default="과업 성격 다름", help="볼 불참 사유(주된 사유에 이 글이 든 공고)")
    ap.add_argument("--with-join", action="store_true", help="참가 공고도 함께(비교용)")
    ap.add_argument("--out", type=Path, default=ROOT / "output" / f"task_nature_{date.today():%Y%m%d}")
    args = ap.parse_args(argv)
    rows = load_rows(args.csv, args.reason, args.with_join)
    if not rows:
        print("볼 공고가 없습니다 — CSV의 '판단'·'주된 사유' 칸을 확인하세요.")
        return 1
    work = Path(tempfile.mkdtemp(prefix="task_nature_"))
    try:
        out = scan(rows, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    csv_path, md_path = write(out, args.out)
    print(f"\n끝: {len(out)}건 → {csv_path}\n         {md_path}  (공개 저장소에 올리지 말 것)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
