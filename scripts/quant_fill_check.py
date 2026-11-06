"""정량평가서 서식 채우기 검사 — 과거 제안요청서 묶음으로 '못 채운 칸'을 센다(2026-11-06 사용자 결정: 규칙 + 검사 묶음).

비공개 레포 jiil-past-contracts의 docs/raw 원본(제안요청서·과업지시서·공고문, .hwp는 python-hwpx로 HWPX로 바꿈)에서
서식을 잘라(forms_cut.cut) 가짜 회사 정보로 채우고(fill_hwpx — 만들기와 같은 규칙: 직접 기입 서식은 서명 줄만, 질의서·공동수급 서식은 뺌),
채운 뒤에도 회사 정보 이름표가 빈 자리(fill.unfilled_slots)를 센다. 규칙을 고칠 때마다 돌려 숫자가 나빠지지 않는지 본다.

    py scripts/quant_fill_check.py --jiil ../jiil-past-contracts            # 전부
    py scripts/quant_fill_check.py --jiil ../jiil-past-contracts --limit 20 # 시험

결과: output/quant_fill_check.csv(서식마다 한 줄 — 공개 금지 폴더), 화면에 요약(빈 자리 종류별 건수·자주 놓치는 서식).
회사 값은 가짜라 결과 파일에 회사 정보는 없다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quantdocs import vault as vault_mod  # noqa: E402
from quantdocs.build import _BLANK_FORM, is_manual  # noqa: E402
from quantdocs.fill import fill_hwpx, unfilled_slots  # noqa: E402
from quantdocs.forms_cut import cut  # noqa: E402

DOC_NAME = re.compile(r"제\s*안\s*요\s*청|과\s*업\s*지\s*시|공\s*고")
FAKE = {"회사명": "(주)가상", "대표자": "홍길동", "주소": "강원특별자치도 정선군 가상로 1", "전화번호": "033-000-0000",
        "팩스번호": "033-000-0001", "사업자등록번호": "000-00-00000", "법인등록번호": "000000-0000000", "이메일": "a@example.com",
        "설립일": "2009-09-23", "대표자 주민등록번호": "800101-1******", "업태": "제조업", "종목": "전시물"}


def to_hwpx(path: Path, work: Path) -> Path | None:
    """원본 → HWPX 경로. .hwp는 python-hwpx로 바꿈(서버 PC 자바 변환기 없이도)."""
    if path.suffix.lower() == ".hwpx":
        try:
            with zipfile.ZipFile(path) as z:
                return path if any(n.startswith("Contents/section") for n in z.namelist()) else None
        except zipfile.BadZipFile:
            pass
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


def docs(raw: Path) -> list[Path]:
    """사업 폴더마다 서식이 든 문서(제안요청서·과업지시서·공고문) — 같은 이름이면 .hwpx 먼저."""
    found: dict[tuple[str, str], Path] = {}
    for p in sorted(raw.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".hwp", ".hwpx") or not DOC_NAME.search(p.name):
            continue
        key = (str(p.parent), p.stem)
        if key not in found or p.suffix.lower() == ".hwpx":
            found[key] = p
    return list(found.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jiil", default=str(ROOT.parent / "jiil-past-contracts"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "output" / "quant_fill_check.csv"))
    args = ap.parse_args(argv)
    raw = Path(args.jiil) / "docs" / "raw"
    files = docs(raw)
    if args.limit:
        files = files[: args.limit]
    v = vault_mod.Vault({}, info=dict(FAKE))
    rows, kinds, by_form, skipped = [], Counter(), Counter(), Counter()
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for i, path in enumerate(files, 1):
            src = to_hwpx(path, work)
            if src is None:
                skipped["열지 못함"] += 1
                continue
            try:
                forms = cut(src, work / f"cut{i}")
            except Exception:  # noqa: BLE001
                skipped["서식 자르기 실패"] += 1
                continue
            if not forms:
                skipped["서식 없음"] += 1
                continue
            for f in forms:
                if _BLANK_FORM.search(f.title):
                    continue
                manual = is_manual(f.title)
                out = work / f"filled{i}_{f.file}"
                try:
                    rep = fill_hwpx(work / f"cut{i}" / f.file, out, v, title="가상 전시관 조성 사업",
                                    g2b_no="R26BK00000000-000", basis=100_000_000, sign_only=manual)
                    with zipfile.ZipFile(out) as z:
                        slots = []
                        for n in z.namelist():
                            if re.fullmatch(r"Contents/section\d+\.xml", n):
                                slots += unfilled_slots(z.read(n).decode("utf-8"), manual)
                except Exception as err:  # noqa: BLE001
                    rows.append([path.parent.name, path.name, f.no, f.title, "오류", 0, str(err)[:120]])
                    continue
                kinds.update(re.sub(r"\s+", "", s) for s in slots)
                if slots:
                    by_form[re.sub(r"\s+", "", f.title)[:20]] += 1
                rows.append([path.parent.name, path.name, f.no, f.title, "직접 기입" if manual else "채움",
                             len(rep.filled), " · ".join(slots)])
            if i % 20 == 0:
                print(f"  {i}/{len(files)} 문서 · {time.time() - started:.0f}초", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["사업 폴더", "문서", "서식 번호", "서식 이름", "구분", "채운 수", "못 채운 칸"])
        w.writerows(rows)
    forms_n = len(rows)
    missed = sum(1 for r in rows if r[6] and r[4] != "오류")
    errors = sum(1 for r in rows if r[4] == "오류")
    print(f"\n문서 {len(files)}개(못 씀 {sum(skipped.values())}: {dict(skipped)}) · 서식 {forms_n}개 · "
          f"못 채운 칸이 있는 서식 {missed}개({missed / max(1, forms_n):.0%}) · 채우다 오류 {errors}개")
    print("못 채운 칸 종류(상위 15):")
    for k, n in kinds.most_common(15):
        print(f"  {n:4d}  {k}")
    print("자주 놓치는 서식(상위 15):")
    for k, n in by_form.most_common(15):
        print(f"  {n:4d}  {k}")
    print(f"자세히: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
