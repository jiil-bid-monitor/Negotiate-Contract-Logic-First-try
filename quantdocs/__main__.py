"""python -m quantdocs list | check --dir <폴더>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .catalog import DEFAULT_CATALOG, load_catalog, scan

PER_NOTICE = {"fixed": "고정", "varies": "공고마다", "when_required": "요구할 때만", "validity": "유효기간 안 고정"}


def _cut_into(sheet, files: list[Path], out_dir: Path, catalog) -> None:
    """공고 문서마다 서식을 잘라 준비표 서식목록에 파일 이름을 적음. 목록에 없던 서식(본문에만 있는 것)은 줄을 더함."""
    from .forms_cut import cut
    from .notice import Form, classify

    by_no = {str(f.no): f for f in sheet.forms}
    for p in files:
        if p.suffix.lower() == ".hwp":
            print(f"  {p.name}: 옛 한글(.hwp)이라 서식을 자르지 않음 — 한글에서 HWPX로 저장한 뒤 다시 실행하세요.")
            continue
        if p.suffix.lower() != ".hwpx":
            continue
        cut_forms = cut(p, out_dir, {k: v.name for k, v in by_no.items()})
        for c in cut_forms:
            key = c.no.split("-")[0]
            f = by_no.get(key)
            if f is None:
                f = Form(int(key), c.title, p.name, False, classify(c.title, catalog))
                sheet.forms.append(f)
                by_no[key] = f
            f.file = ", ".join(x for x in [f.file, c.file] if x)
        print(f"  {p.name}: 서식 {len(cut_forms)}개 → {out_dir}")
    sheet.forms.sort(key=lambda f: f.no)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantdocs", description="정량평가서 서류 준비")
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="서류 목록 JSON")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="서류 목록과 자동화 범위")
    check = sub.add_parser("check", help="폴더에 서류별 파일이 있는지")
    check.add_argument("--dir", required=True, help="회사 서류 폴더(예: 회사서류_최신)")
    tmpl = sub.add_parser("templates", help="담당자가 채울 빈 엑셀 양식 만들기")
    tmpl.add_argument("--out", default=".", help="양식을 만들 폴더(기본: 지금 폴더)")
    nt = sub.add_parser("notice", help="공고 자료를 읽어 공고별 준비표(엑셀) 만들기")
    nt.add_argument("files", nargs="+", help="공고문·제안요청서 등(hwp/hwpx/pdf)")
    nt.add_argument("--out", default=".", help="준비표를 만들 폴더")
    nt.add_argument("--company", help="채운 회사자료 양식(실적 후보를 고를 때)")
    nt.add_argument("--notice-date", help="실적 기간 기준일 YYYY-MM-DD(공고일, 기본 오늘)")
    nt.add_argument("--cut-forms", action="store_true", help="HWPX 안의 첨부 서식을 서식별 파일로 잘라 <out>/서식/에")
    fm = sub.add_parser("forms", help="HWPX 공고 문서 안의 첨부 서식을 서식별 파일로 자르기")
    fm.add_argument("file", help="제안요청서 등(.hwpx — .hwp는 먼저 HWPX로 저장)")
    fm.add_argument("--out", default=".", help="잘라 낸 서식을 둘 폴더")
    im = sub.add_parser("import", help="회사 원본 서류(수주현황 엑셀·법인일괄서류·직접생산증명서)를 회사자료 폴더로")
    im.add_argument("--out", required=True, help="만들 회사자료 폴더(회사자료_양식.xlsx + files/)")
    im.add_argument("--orders", help="공사수주현황 엑셀")
    im.add_argument("--corp", help="법인일괄서류 폴더 또는 zip")
    im.add_argument("--direct", help="직접생산확인증명서 폴더 또는 zip")
    im.add_argument("--registration", help="경쟁입찰참가자격등록증 PDF(나라장터 출력본)")
    vt = sub.add_parser("vault", help="지일 회사 자료 잠금 묶음(vault/company.enc) 만들기·확인")
    vt.add_argument("action", choices=["seal", "list", "check"],
                    help="seal: 폴더를 잠가 묶음 만들기, list: 묶음 안 파일 목록, check: 서식에 넣을 회사정보를 어디서 찾는지 점검")
    vt.add_argument("--src", help="seal할 회사자료 폴더(회사자료_양식.xlsx + files/)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    catalog = load_catalog(Path(args.catalog))
    if args.cmd == "list":
        for i, d in enumerate(catalog, 1):
            print(f"{i}. {d.name} [{PER_NOTICE.get(d.per_notice, d.per_notice)}] — 자동화: {d.automation}"
                  + (f" / 사람: {d.human}" if d.human else ""))
        return 0
    if args.cmd == "templates":
        from .templates import build
        try:
            out = build(Path(args.out), catalog)
        except FileExistsError as err:
            print(err, file=sys.stderr)
            return 1
        print(f"빈 양식을 만들었습니다: {out}")
        return 0
    if args.cmd == "notice":
        from datetime import datetime

        from .notice import read_notice, write_sheet
        sheet = read_notice([Path(f) for f in args.files], catalog)
        if args.cut_forms:
            _cut_into(sheet, [Path(f) for f in args.files], Path(args.out) / "서식", catalog)
        ref = datetime.strptime(args.notice_date, "%Y-%m-%d").date() if args.notice_date else None
        out = write_sheet(sheet, Path(args.out), Path(args.company) if args.company else None, ref)
        print(f"서식 {len(sheet.forms)} (정량평가서 {len(sheet.quant_items)}) · 제출서류 {len(sheet.submit_items)} · "
              f"실적 요건 {len(sheet.track_reqs)} · 마감 {len(sheet.deadlines)}")
        print(f"준비표: {out}")
        return 0
    if args.cmd == "import":
        from .importer import run
        try:
            rep = run(Path(args.out), args.orders and Path(args.orders), args.corp and Path(args.corp),
                      args.direct and Path(args.direct), registration=args.registration and Path(args.registration))
        except FileExistsError as err:
            print(err, file=sys.stderr)
            return 1
        print(f"실적 {rep.tracks}건 · 서류 {rep.docs}줄(직접생산 품목 {rep.direct_items}) · 회사정보 {', '.join(rep.info) or '-'}")
        if rep.registration:
            print(f"참가자격등록증: 업종 {rep.registration[0]}개 · 등록 품목 {rep.registration[1]}개")
        for w in rep.warnings:
            print(f"  ! {w}")
        print(f"→ {args.out} — 엑셀을 확인·보완한 뒤 `python -m quantdocs vault seal --src \"{args.out}\"`")
        return 0
    if args.cmd == "vault":
        import getpass
        import os

        from . import vault
        pw = vault.read_passphrase()
        if pw:
            print(f".env의 {vault.ENV}를 씀(웹앱과 같은 비밀번호)")
        else:
            pw = getpass.getpass("회사 자료 묶음 비밀번호: ")
        try:
            if args.action == "seal":
                if not args.src:
                    print("--src 로 회사자료 폴더를 주세요", file=sys.stderr)
                    return 1
                out = vault.seal_folder(Path(args.src), pw)
                vault.load(pw, out)  # 바로 열어 봄
                print(f"잠금 묶음을 만들었습니다: {out} — 이 파일만 커밋하세요(원본 폴더는 커밋하지 않음)")
            elif args.action == "check":
                from .importer import enrich_vault
                v = vault.load(pw)
                sheet = dict(v.info)
                rows: list = []
                notes = enrich_vault(v, rows)
                print("묶음 안 PDF — 읽은 방법 · 알아본 서류 종류")
                for name, how, kinds in rows:
                    print(f"  {name} — {how} · {kinds or '-'}")
                print("서식에 넣는 회사정보 — 값 (출처)")
                for k in ("회사명", "대표자", "주소", "법인등록번호", "사업자등록번호", "전화번호", "팩스번호", "대표자 주민등록번호"):
                    val = v.info.get(k, "")
                    src = "회사정보 시트" if sheet.get(k) else ("서류 PDF" if val else "")
                    print(f"  {k}: {val or '(없음)'}" + (f"  ({src})" if src else ""))
                for n in notes:
                    print("※ " + n)
            else:
                v = vault.load(pw)
                print(f"파일 {len(v.files)}개 · 회사정보 {len(v.info)}항목 · 연혁 {len(v.history)} · 서류 {len(v.docs)} · 실적 {len(v.tracks)}")
                for name in sorted(v.files):
                    print(f"  {name} ({len(v.files[name]):,} 바이트)")
        except vault.VaultError as err:
            print(err, file=sys.stderr)
            return 1
        return 0
    if args.cmd == "forms":
        from .forms_cut import cut
        try:
            forms = cut(Path(args.file), Path(args.out))
        except ValueError as err:
            print(err, file=sys.stderr)
            return 1
        for f in forms:
            print(f"서식{f.no}: {f.file}")
        print(f"서식 {len(forms)}개 → {args.out}" if forms else "서식 제목([서식 N] 등)을 찾지 못했습니다.")
        return 0

    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"폴더가 없습니다: {folder}", file=sys.stderr)
        return 1
    found = scan(folder, catalog)
    missing = 0
    for d in catalog:
        files = found[d.id]
        if not d.filename_keywords:
            print(f"- {d.name}: 파일로 확인하지 않음 ({d.automation})")
        elif files:
            print(f"✓ {d.name}: {len(files)}개 — " + ", ".join(f.name for f in files[:3]) + (" …" if len(files) > 3 else ""))
        else:
            missing += 1
            print(f"✗ {d.name}: 못 찾음 (파일 이름 낱말: {', '.join(d.filename_keywords)})")
    print(f"\n못 찾은 서류 {missing}종 — 파일 이름이 다르면 config/documents.json의 filename_keywords를 고치세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
