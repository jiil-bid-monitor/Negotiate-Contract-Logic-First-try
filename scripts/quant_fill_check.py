"""정량평가서 서식 채우기 검사 — 과거 제안요청서 묶음으로 '못 채운 칸'을 센다(2026-11-06 사용자 결정: 규칙 + 검사 묶음).

비공개 레포 jiil-past-contracts의 docs/raw 원본(제안요청서·과업지시서·공고문, .hwp는 python-hwpx로 HWPX로 바꿈)에서
사업 폴더마다 `quantdocs.health.check`(새 공고 미리 검사와 같은 검사)로 서식 목록 ↔ 잘린 서식, 자기평가표·배점기준,
가짜 회사 정보로 채운 뒤 빈 칸·모르는 이름표를 센다(11/10 — 예전엔 빈 칸만). 규칙을 고칠 때마다 돌려 숫자가 나빠지지 않는지 본다.

    py scripts/quant_fill_check.py --jiil ../jiil-past-contracts            # 전부
    py scripts/quant_fill_check.py --jiil ../jiil-past-contracts --limit 20 # 시험

결과: output/quant_fill_check.csv(문제마다 한 줄 — 공개 금지 폴더), 화면에 요약(문제 종류별 건수·걸린 사업 수).
회사 값은 가짜라 결과 파일에 회사 정보는 없다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdocs import health  # noqa: E402

DOC_NAME = re.compile(r"제\s*안\s*요\s*청|과\s*업\s*지\s*시|공\s*고")


def to_hwpx(path: Path, work: Path) -> Path | None:
    """옛 한글(.hwp) → HWPX(python-hwpx — 서버 PC 자바 변환기 없이도). health.check의 convert로 씀."""
    try:
        from hwpx.hwp5.package import convert, to_hwpx_bytes
    except ImportError:
        return None
    try:
        dst = work / (path.stem + ".hwpx")
        dst.write_bytes(to_hwpx_bytes(convert(path.read_bytes()).files))
        return dst
    except Exception:  # noqa: BLE001 — 깨진 문서·보안 문서는 건너뜀
        return None


def projects(raw: Path) -> list[list[Path]]:
    """사업 폴더마다 서식이 든 문서(제안요청서·과업지시서·공고문) 묶음 — 같은 이름이면 .hwpx 먼저.
    공고문이 적은 서식 목록이 제안요청서에 있는 경우가 많아 폴더 단위로 검사(실제 만들기와 같게)."""
    found: dict[tuple[str, str], Path] = {}
    for p in sorted(raw.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".hwp", ".hwpx") or not DOC_NAME.search(p.name):
            continue
        key = (str(p.parent), p.stem)
        if key not in found or p.suffix.lower() == ".hwpx":
            found[key] = p
    groups: dict[tuple[str, ...], list[Path]] = {}
    for _, p in sorted(found.items()):  # docs/raw/<연도>/<사업>/<제안요청서·공고문…>/파일
        groups.setdefault(p.relative_to(raw).parts[:2], []).append(p)
    return list(groups.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jiil", default=str(ROOT.parent / "jiil-past-contracts"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "output" / "quant_fill_check.csv"))
    args = ap.parse_args(argv)
    raw = Path(args.jiil) / "docs" / "raw"
    groups = projects(raw)
    if args.limit:
        groups = groups[: args.limit]
    rows, kinds, slot_kinds, by_form, notes = [], Counter(), Counter(), Counter(), Counter()
    projects_hit: dict[str, set[str]] = {}
    forms_n = filled_forms = 0
    started = time.time()
    for i, group in enumerate(groups, 1):
        folder = "/".join(group[0].relative_to(raw).parts[:2])
        with tempfile.TemporaryDirectory() as tmp:  # 사업마다 지움(11/10 — 끊긴 검사가 /tmp에 11GB를 남겼음)
            h = health.check(group, Path(tmp), convert=to_hwpx)
        forms_n += len(h.forms)
        filled_forms += sum(1 for f in h.forms if f[2])
        for n in h.notes:
            notes[re.sub(r"^.*?: ", "", n)[:30]] += 1
        for it in h.issues:
            kinds[it.kind] += 1
            projects_hit.setdefault(it.kind, set()).add(folder)
            if it.kind in ("빈 칸", "모르는 이름표"):
                slot_kinds[f"{it.kind} · {re.sub(r'\s+', '', it.detail)}"] += 1
                by_form[re.sub(r"\s+", "", it.form)[:24]] += 1
            rows.append([folder, " · ".join(p.name for p in group), it.kind, it.form, it.detail])
        if not h.issues:
            rows.append([folder, " · ".join(p.name for p in group), "이상 없음", "", f"서식 {len(h.forms)}개"])
        if i % 20 == 0:
            print(f"  {i}/{len(groups)} 사업 · {time.time() - started:.0f}초", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["사업 폴더", "문서", "검사", "서식", "내용"])
        w.writerows(rows)
    print(f"\n사업 {len(groups)}개 · 서식 {forms_n}개(값을 넣은 서식 {filled_forms}개) · {time.time() - started:.0f}초")
    print("문제 종류(건수 / 걸린 사업 수):")
    for k in health.KINDS:
        print(f"  {kinds.get(k, 0):5d} / {len(projects_hit.get(k, ())):3d}  {k}")
    print("빈 칸·모르는 이름표(상위 20):")
    for k, n in slot_kinds.most_common(20):
        print(f"  {n:4d}  {k}")
    print("자주 걸리는 서식(상위 10):")
    for k, n in by_form.most_common(10):
        print(f"  {n:4d}  {k}")
    if notes:
        print("검사 알림(변환 실패 등):", dict(notes.most_common(8)))
    print(f"자세히: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
