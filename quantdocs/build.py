"""공고 자료 + 회사 자료(잠금 묶음) → 정량평가서 묶음(zip).

    analyze(files, vault)  → 화면에 보일 요약(서식·제출서류·자격·실적 후보)
    build(files, vault, out_zip) → 채운 서식 + 첨부할 회사 서류 + 준비표 + 공고 원문을 zip 하나로

옛 한글(.hwp)은 환경변수 HWP2HWPX_CMD(예: `java -jar C:/tools/hwp2hwpx-cli.jar "{src}" "{dst}"`)로 HWPX로 바꿔 자른다.
설정이 없거나 실패하면 그 문서의 서식은 못 자르고 준비표에 이유를 적는다.
범위: 회사 연혁·실적·보유 자격(직접생산 포함)만 채운다(11/03 사용자 결정) — 나머지 서류는 준비표에 '준비 필요'.
대상: 협상뿐 아니라 정량 서류를 내는 공고 전반(적격심사·사업수행능력평가·종합심사·규격가격동시입찰 — 11/03 사용자 결정으로 넓힘).
평가 점수는 계산하지 않는다.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .bundle import merge, pick
from .catalog import load_catalog
from .fill import FillReport, fill_hwpx
from .forms_cut import cut, merge as merge_forms, page_titles
from .g2b import qualification_rows
from .kinds import detect, guide
from .notice import Form, classify, form_tracks, read_notice, track_candidates, write_sheet

SCOPE_DOCS = {"history", "track_record", "qualification", "direct_production"}  # 이번 단계에서 채우는 서류 종류


@dataclass
class Analysis:
    sheet: object
    quals: list[dict]
    candidates: list[dict]  # 첫 실적 요건 기준 후보
    converted: dict[str, str] = field(default_factory=dict)  # .hwp 이름 → 바꾼 .hwpx 경로
    notes: list[str] = field(default_factory=list)
    kind: str = "other"  # 공고 유형(kinds.detect)
    picks: list = field(default_factory=list)  # 통합본에 넣을 회사 서류(bundle.Pick, 파일 이름 키워드로 고름)
    pick_missing: list[str] = field(default_factory=list)


def _is_hwpx(p: Path) -> bool:
    try:
        with zipfile.ZipFile(p) as z:
            return any(n.startswith("Contents/section") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def convert_hwp(src: Path, work: Path) -> tuple[Path | None, str]:
    """HWP2HWPX_CMD로 .hwp → .hwpx. (결과, 실패 이유).

    명령은 먼저 낱말로 나눈 뒤 {src}·{dst}를 넣고 셸 없이 실행.
    자바가 윈도우에서 한글 경로를 깨뜨려(11/03 서버 PC 실측 — FileNotFoundException) 영문 이름 임시 폴더에
    in.hwp로 복사해 변환하고 결과만 옮겨 온다."""
    from .vault import read_env

    tmpl = read_env("HWP2HWPX_CMD").strip().strip("'")
    if not tmpl:
        return None, "변환기 미설정"
    dst = work / (src.stem + ".hwpx")
    tmp = Path(tempfile.mkdtemp(prefix="hwp2hwpx_"))
    try:
        t_src, t_dst = tmp / "in.hwp", tmp / "out.hwpx"
        shutil.copyfile(src, t_src)
        try:
            args = [a.replace("{src}", str(t_src)).replace("{dst}", str(t_dst)) for a in shlex.split(tmpl)]
        except ValueError as err:
            return None, f"HWP2HWPX_CMD 형식 오류: {err}"
        try:
            r = subprocess.run(args, capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            return None, "3분 안에 끝나지 않음"
        except OSError as err:
            return None, f"실행 못 함({args[0]}): {err}"
        if t_dst.exists() and zipfile.is_zipfile(t_dst):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(t_dst), dst)
            return dst, ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    raw = r.stderr or r.stdout or b""
    try:
        err = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        err = raw.decode("cp949", "replace").strip()
    lines = [ln.strip() for ln in err.splitlines() if ln.strip() and not ln.strip().startswith("at ")]
    return None, (lines[0][:200] if lines else f"종료 코드 {r.returncode}")


def _use_page_titles(sheet, hwpx_files: list[Path], source: str) -> None:
    """서식 이름 = 서식 페이지의 제목(가장 큰 글씨) 그대로(11/04 사용자 결정 — 목록·목차·'다음 줄' 짐작보다 우선).
    공고 목록에 없던 서식도 더함. 한글 파일(HWPX·변환본)이 있을 때만 — 없으면 목록에서 읽은 이름 그대로."""
    by_no = {f.no: f for f in sheet.forms}
    for p in hwpx_files:
        if p.suffix.lower() != ".hwpx" or not p.exists():
            continue
        try:
            titles = page_titles(p, source)
        except (zipfile.BadZipFile, KeyError, ValueError):
            continue
        for no, title in titles.items():
            doc = classify(title, load_catalog())
            if no in by_no:
                f = by_no[no]
                f.name, f.doc, f.weak = title, doc, False
            else:
                by_no[no] = Form(no, title, p.name, False, doc)
    sheet.forms = sorted(by_no.values(), key=lambda f: f.no)


def analyze(files: list[Path], vault, notice_date: date | None = None, work: Path | None = None,
            award_method: str = "", contract_method: str = "") -> Analysis:
    files = [Path(f) for f in files]
    work = Path(work or tempfile.mkdtemp(prefix="quantdocs_"))
    work.mkdir(parents=True, exist_ok=True)  # 웹앱이 넘기는 작업 폴더(data/jobs/<id>/work)는 아직 없을 수 있음
    notes, converted, readable = [], {}, []
    for f in files:
        if f.suffix.lower() == ".hwp" and _is_hwpx(f):
            # 이름만 .hwp이고 속은 HWPX(zip)인 첨부 — 변환 없이 그대로(11/03 삼척선 동준교 공고)
            hx = work / (f.stem + ".hwpx")
            shutil.copyfile(f, hx)
            converted[f.name] = str(hx)
        elif f.suffix.lower() == ".hwp":
            hx, why = convert_hwp(f, work)
            if hx:
                converted[f.name] = str(hx)
            elif why == "변환기 미설정":
                notes.append(f"{f.name}: 옛 한글(.hwp) — 변환기가 설정되지 않아 서식을 자르지 못함(목록 읽기는 됨) — 서버 PC에서 bash tools/install_hwp2hwpx.sh")
            else:
                notes.append(f"{f.name}: HWPX 변환 실패({why}) — 서식을 자르지 못함(목록 읽기는 됨)")
        readable.append(f)
    sheet = read_notice(readable, load_catalog())
    _use_page_titles(sheet, [Path(converted.get(f.name, f)) for f in files], " ".join(f.name for f in files))
    quals = qualification_rows(readable, vault.held_codes(), vault)
    ref = notice_date or date.today()
    candidates = []
    if sheet.track_reqs:
        req = next((t for t in sheet.track_reqs if t.kind == "제출 요건"), sheet.track_reqs[0])
        candidates = track_candidates(req, vault.tracks, ref)
        candidates.sort(key=lambda t: -(t.get("인정 금액(원)") or 0))
    kind = detect(award_method, contract_method, sheet.texts)
    if kind != "negotiation" and not sheet.forms:
        notes.append(f"{guide(kind)['name']} 공고 — 첨부에서 서식 목록을 찾지 못함. 서식은 {guide(kind)['source']}")
    picks, pick_missing = pick(list(vault.files), [it.text for it in sheet.submit_items], quals)
    return Analysis(sheet, quals, candidates, converted, notes, kind, picks, pick_missing)


def summary(a: Analysis) -> dict:
    """화면용 JSON."""
    sh = a.sheet
    g = guide(a.kind)
    by_id = {d.id: d for d in load_catalog()}
    return {
        "kind": {"id": a.kind, "name": g["name"], "when": g["when"], "source": g["source"],
                 "docs": [{"name": by_id[i].name, "auto": i in SCOPE_DOCS} for i in g["docs"] if i in by_id]},
        "forms": [{"no": f.no, "name": f.name, "quant": f.in_quant, "kind": f.doc.name if f.doc else "공고 서식(직접 작성)",
                   "manual": is_manual(f.name),
                   "auto": not is_manual(f.name) and (bool(f.doc and f.doc.id in SCOPE_DOCS) or _is_basic(f.name))}
                  for f in sh.forms],
        "submit": [{"text": it.text, "kind": it.doc.name if it.doc else ""} for it in sh.submit_items],
        "quals": a.quals,
        "tracks": [{"text": t.text, "kind": t.kind, "years": t.years, "min": t.min_amount, "per": t.per_project}
                   for t in sh.track_reqs],
        "candidates": [{"name": c.get("사업명"), "org": c.get("발주기관"), "done": str(c.get("준공일") or "")[:10],
                        "amount": c.get("인정 금액(원)"), "check": c.get("확인할 것")} for c in a.candidates[:20]],
        "deadlines": [[k, f"{w:%Y-%m-%d %H:%M}"] for k, w, _ in sh.deadlines],
        "notes": a.notes,
        "bundle": [{"section": p.section, "file": Path(p.path).name, "note": p.note} for p in a.picks],
        "bundle_missing": a.pick_missing,
    }


# 공고마다 사람이 정하는 값(담당 PM·투입 인력·조직·경력·자기평가 점수)을 적는 서식 — 회사 자료로 채우지 않고 제안요청서 양식 그대로 넣음
# (11/03 사용자 요청: 통합본 받은 뒤 직접 기입). 이 서식의 '주소·전화' 같은 칸은 사람 것이라 회사 값을 넣으면 틀림.
_MANUAL_FORM = re.compile(r"인력|인원|PM|책임자|투입|이력|경력|참여자|조직|재직|자격\s*사항|자기\s*평가|실적\s*증명")


def is_manual(name: str) -> bool:
    return bool(_MANUAL_FORM.search(name or ""))


def _is_basic(name: str) -> bool:
    """회사 기본 정보만 채우면 되는 서식(신청서·제출서·표지 등) — 회사명·대표자 칸이 있음."""
    return bool(re.search(r"신청서|제출서|표지|일반현황|위임장|선임계|서약서", name))


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip() or "파일"


def build(files: list[Path], vault, out_zip: Path, notice_title: str = "", notice_date: date | None = None,
          award_method: str = "", contract_method: str = "") -> Path:
    files = [Path(f) for f in files]
    work = Path(tempfile.mkdtemp(prefix="quantdocs_build_"))
    try:
        a = analyze(files, vault, notice_date, work, award_method, contract_method)
        forms_dir, filled_dir = work / "cut", work / "서식"
        reports: list[FillReport] = []
        manual: list[str] = []  # 양식 그대로 넣은 서식 파일
        manual_dir = work / "서식_직접기입"
        merged_forms: list[Path] = []  # 공고 문서마다 서식 전체를 이은 한글 파일
        merge_failed = False  # 잇지 못한 문서가 있으면 서식별 파일도 묶음에 넣음
        cut_names = {str(f.no): f.name for f in a.sheet.forms}
        for f in files:
            src = Path(a.converted.get(f.name, f))
            if src.suffix.lower() != ".hwpx":
                continue
            parts = []
            for c in cut(src, forms_dir, cut_names):
                if is_manual(c.title):  # 양식 그대로 — 내려받은 뒤 직접 기입
                    manual_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(forms_dir / c.file, manual_dir / c.file)
                    manual.append(c.file)
                    parts.append((c, manual_dir / c.file))
                    continue
                is_track = bool(re.search(r"실적.*(집계|현황)|수행실적", c.title))
                rows = a.candidates
                if is_track and not rows:  # 공고에 실적 요건이 없으면 서식 제목(분야·최근 N년)으로 고름(11/04 사용자 결정)
                    rows, why = form_tracks(c.title, vault.tracks, notice_date or date.today())
                    a.notes += [f"{c.file}: {w}" for w in why]
                rep = fill_hwpx(forms_dir / c.file, filled_dir / c.file, vault, rows if is_track else None)
                reports.append(rep)
                parts.append((c, filled_dir / c.file))
            if parts:  # 서식 전체(채운 것 + 직접 기입)를 한글 파일 하나로 — 담당자가 이 파일 하나에서 기입(11/03 사용자 요청)
                parts.sort(key=lambda x: [int(n) for n in re.findall(r"\d+", x[0].no)])
                try:
                    merged_forms.append(merge_forms(src, parts, work / "통합서식" / f"통합서식_{_safe(Path(f.name).stem)[:40]}.hwpx"))
                except (ValueError, zipfile.BadZipFile, KeyError) as err:
                    merge_failed = True
                    a.notes.append(f"{f.name}: 서식을 한 파일로 잇지 못함({err}) — 서식별 파일을 쓰세요")
        per_form = merge_failed or not merged_forms
        # 첨부할 회사 서류: 파일 이름 키워드로 고른 것(통합본 순서대로 번호) + 연혁·실적 증빙(양식에 적힌 파일 이름)
        attach: list[tuple[str, str, bytes]] = []  # (폴더, 이름, 내용)
        missing: list[str] = list(a.pick_missing)

        def add(folder: str, fname, why: str) -> None:
            data = vault.file(fname)
            if data is None:
                missing.append(f"{why}: 파일 '{fname}'을 묶음에서 못 찾음")
                return
            name = _safe(str(fname).replace("\\", "/").rsplit("/", 1)[-1])
            if all(n != name or fo != folder for fo, n, _ in attach):
                attach.append((folder, name, data))

        for i, p in enumerate(a.picks, 1):
            attach.append(("첨부/통합본_순서", f"{i:02d}_{_safe(Path(p.path).name)}", vault.files[p.path]))
            if p.note:
                missing.append(f"{Path(p.path).name}: {p.note}")
        merged, skipped = merge(a.picks, vault, work / "통합본.pdf",
                                f"{notice_title or '정량평가서'} — 제출 서류 통합본") if a.picks else (None, [])
        missing += skipped
        for h in vault.history:
            if h.get("증빙 파일 이름"):
                add("첨부/연혁", h["증빙 파일 이름"], "연혁 증빙")
        for t in a.candidates:
            if t.get("증빙 파일 이름"):
                add("첨부/실적", t["증빙 파일 이름"], f"실적 {t.get('사업명')}")

        def extra(wb, table):
            q_rows = []
            for i, q in enumerate(a.quals, 1):
                for it in q["items"]:
                    q_rows.append([i, q["kind"], f"{it['name']}({it['code']})" if it["code"] else it["name"],
                                   "보유" if it["held"] else "미보유", ", ".join(it["docs"]), q["source"]])
            table(wb.create_sheet("자격요건"), ["묶음", "조건", "자격", "지일", "첨부 서류", "출처"],
                  q_rows or [["", "", "첨부 공고문에서 코드로 적힌 자격을 찾지 못함 — 원문 확인", "", "", ""]],
                  [6, 10, 40, 8, 40, 30])
            f_rows = [[r.file, "\n".join(r.filled) or "-", r.rows or "",
                       (f"{r.overflow}건 (금액이 작은 것): " + ", ".join(r.overflow_names[:10])) if r.overflow_names
                       else (r.overflow or "")] for r in reports]
            table(wb.create_sheet("통합본 순서"), ["순서", "구분", "파일", "확인"],
                  [[i, p.section, Path(p.path).name, p.note] for i, p in enumerate(a.picks, 1)]
                  or [["", "파일 이름이 맞는 회사 서류가 없음", "", ""]], [6, 40, 40, 36])
            table(wb.create_sheet("자동입력"), ["서식 파일", "채운 칸 (확인 필요)", "넣은 실적", "줄 모자라 못 넣은 실적"],
                  f_rows or [["서식을 자르지 못함", "", "", ""]], [40, 60, 10, 14])
            g = guide(a.kind)
            table(wb.create_sheet("공고 유형"), ["항목", "내용"],
                  [["유형", g["name"]], ["제출 시점", g["when"]], ["서식이 있는 곳", g["source"]],
                   ["기본 준비 서류", ", ".join(d.name for d in load_catalog() if d.id in g["docs"])],
                   ["안내", "공고문·세부기준이 우선 — 위는 공고에서 못 찾을 때의 출발점"]], [16, 90])
            todo = [[d.name, d.human or "-", "이번 단계 범위 밖 — 직접 준비"] for d in load_catalog()
                    if d.id not in SCOPE_DOCS and d.id in g["docs"] + ["reissue"]]
            todo += [[(f"서식_직접기입/{m}" if per_form else f"통합서식.hwpx — {m.rsplit('.', 1)[0]}"),
                      "제안요청서 양식 그대로 — 담당 PM·투입 인력 등 직접 기입", "직접 기입"] for m in manual]
            todo += [[m, "", "묶음 확인"] for m in missing] + [[n, "", "참고"] for n in a.notes]
            table(wb.create_sheet("준비 필요"), ["서류", "할 일", "비고"], todo, [40, 40, 30])

        sheet_path = write_sheet(a.sheet, work, ref=notice_date, tracks=vault.tracks, extra=extra)
        out_zip = Path(out_zip)
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(sheet_path, "공고별_준비표.xlsx")
            if merged:
                z.write(merged, "통합본.pdf")
            for p in merged_forms:  # 문서가 하나면 '통합서식.hwpx'
                z.write(p, "통합서식.hwpx" if len(merged_forms) == 1 else p.name)
            if per_form:  # 통합서식을 못 만든 경우만 서식별 파일(11/04 사용자 결정: 한글 통합본만)
                for p in sorted(filled_dir.glob("*.hwpx")) if filled_dir.exists() else []:
                    z.write(p, f"서식/{p.name}")
                for p in sorted(manual_dir.glob("*.hwpx")) if manual_dir.exists() else []:
                    z.write(p, f"서식_직접기입/{p.name}")
            for folder, name, data in attach:
                z.writestr(f"{folder}/{name}", data)
            for f in files:
                z.write(f, f"공고문/{f.name}")
            z.writestr("읽어보기.txt", _readme(f"{notice_title} [{guide(a.kind)['name']}]", reports, attach, missing, a.notes, manual,
                                              per_form))
        return out_zip
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _readme(title: str, reports, attach, missing, notes, manual=(), per_form: bool = True) -> str:
    lines = [f"정량평가서 준비 묶음 — {title}".strip(), "",
             "· 통합서식.hwpx : 공고 서식 전체를 서식 순서대로 이은 한글 파일 하나 — 회사 정보·연혁·실적을 채운 서식과 "
             "담당 PM·투입 인력·자기평가처럼 공고마다 정하는 값을 적는 서식(제안요청서 양식 그대로)이 함께 있음, 이 파일에서 바로 기입 "
             "— 자동으로 채운 칸은 준비표 '자동입력' 시트에서 확인"]
    if per_form:
        lines += ["· 서식/ : 서식별로 잘라 채운 것(통합서식을 다 잇지 못해 함께 넣음)",
                  "· 서식_직접기입/ : 직접 기입할 서식(제안요청서 양식 그대로)"]
    lines += ["· 통합본.pdf : (회사 서류 스캔본) 공고 제출서류·요구 자격에 맞는 회사 서류를 파일 이름 키워드로 골라 원본 그대로 이어 붙인 것(목차·책갈피) — 순서는 준비표 '통합본 순서'",
              "· 첨부/ : 통합본에 넣은 원본(순서 번호) + 연혁·실적 증빙 — 제출 전 유효기간 확인",
              "· 공고별_준비표.xlsx : 서식 목록·제출서류·자격요건·실적요건·실적후보·준비 필요 서류",
              "· 실적후보는 후보일 뿐 — 제출할 실적은 담당자가 확정", "· 이 묶음엔 회사 서류가 들어 있으니 사내에서만 보관",
              "", f"채운 서식 {len(reports)}개, 직접 기입 서식 {len(manual)}개, 첨부 서류 {len(attach)}개"]
    lines += [f"! {m}" for m in missing] + [f"※ {n}" for n in notes]
    return "\r\n".join(lines)
