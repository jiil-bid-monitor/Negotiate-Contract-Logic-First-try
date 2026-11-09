"""1억 미만 입찰(전시관·체험관) 공고 폴더 자동 생성 (2026-11-04 사용자 결정. 협상은 11-05 시험 뒤 뺌).

새 공고마다 결과 폴더를 표본(1억미만_사업폴더_취합_20261028)과 같은 구조로 만들고 개발자 방에 알린다:
  <공고>/  최신 공고문·첨부(맨 위) · 공고정보_요약.pdf · 서식자동분리/ · 회사준비서류/ · 이전공고/
재공고·정정공고는 새 폴더를 만들지 않고 같은 폴더의 맨 위 문서를 이전공고로 옮긴 뒤 새 문서를 맨 위에 둔다.
회사 값은 NAS 공유 폴더의 회사 프로필·회사 서류 폴더에서만 읽어 이 코드엔 회사 정보가 없다. 평가 점수·투찰 금액은 다루지 않는다.

    python -m under100m --out <결과 폴더> [--days 3] [--no-notify]

NAS에서는 공유 폴더 under100m/run.sh가 이 명령을 부른다(scripts/nas/under100m_run.sh가 1시간마다 실행).
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .docs import _unique, issue_lines
from .form_fill import fill_form, load_company
from .form_split import split_forms
from .pdf_forms import fill_pdf, split_pdf
from .prep import PREP_DIR, prepare
from .summary import write_summary_pdf

log = logging.getLogger("under100m")

TARGET_WORDS = ("전시관", "체험관")
LIMIT = 100_000_000
MAX_TRIES = 3
FORMS_DIR = "서식자동분리"
PREV_DIR = "이전공고"
SUMMARY = "공고정보_요약.pdf"
FORMS_LIST = "제출서류_체크리스트.txt"
SAME_TITLE_DAYS = 180  # 공고명·기관이 같아도 이보다 오래되면 다른 해의 사업으로 본다
COMPANY_DOCS = Path(os.environ.get("COMPANY_DOCS_DIR") or "/work/회사서류_최신")
COMPANY_PROFILE = Path(os.environ.get("COMPANY_PROFILE") or "/work/under100m/회사프로필.json")
_AMOUNT = re.compile(r"(기초\s*금액|소요\s*예산|사업\s*예산|사\s*업\s*비|사업\s*금액)"
                     r"[^0-9\n]{0,30}?(?:금\s*)?([0-9]{1,3}(?:,[0-9]{3})+)\s*(천원|원)?")
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]')
_RE_NOTICE = re.compile(r"[(\[【<]?\s*(재\s*공고|재\s*입찰|정정|변경|긴급|\d+\s*차)\s*(공고)?\s*[)\]】>]?")
_DOC_FILE = re.compile(r"공고|제안요청|과업|입찰안내|제출서류")
# 파일 전체가 서식 하나인 첨부(안에 서식 표시 없음) — 입찰서·서약서 등. 짧은 문서만(2026-11-05 인하대·암센터)
_WHOLE_FORM = re.compile(r"서약서|각서|입찰서|신청서|위임장|확인서|동의서|협정서|인감계|증명서")
_NOT_WHOLE_FORM = re.compile(r"공고|제안요청|과업|규격|시방|유의서|조건|기준|사유서|내역|명세|안내|설명")
WHOLE_FORM_CHARS = 6000


def is_target(candidate) -> bool:
    """본공고 후보 중 공고명에 전시관·체험관이 있는 공고(띄어쓰기 무시). 유형은 `collect`가 받을 때 거른다."""
    title = re.sub(r"\s+", "", candidate.notice.title or "")
    return bool(candidate.is_candidate and candidate.notice.kind == "본공고" and any(w in title for w in TARGET_WORDS))


def business_amount(notice, text: str) -> tuple[float | None, str]:
    """사업금액(부가세 포함) — 공고문 사업비·사업예산·사업금액·소요예산 > 나라장터 배정예산 > 공고문 기초금액 > 추정가격×1.1.
    추정가격 기준으로 보면 추정 0.94억·사업금액 1.039억 공고가 1억 미만으로 잘못 들어감(10/29 사용자 정정)."""
    found: dict[str, float] = {}
    for m in _AMOUNT.finditer(text or ""):
        value = float(m.group(2).replace(",", "")) * (1000 if m.group(3) == "천원" else 1)
        if value >= 1_000_000:
            found.setdefault(re.sub(r"\s", "", m.group(1)), value)
    for key in ("사업비", "사업예산", "사업금액", "소요예산"):
        if key in found:
            return found[key], f"공고문 {key}"
    if (notice.assigned_budget or 0) >= 1000:
        return notice.assigned_budget, "나라장터 배정예산"
    if "기초금액" in found:
        return found["기초금액"], "공고문 기초금액"
    if (notice.estimated_price or 0) >= 1000:
        return notice.estimated_price * 1.1, "추정가격×1.1"
    return None, ""


def notice_key(notice) -> str:
    return f"{notice.notice_no}-{notice.notice_ord or '000'}"


def folder_name(notice) -> str:
    title = re.sub(r"\s+", " ", _UNSAFE.sub("_", notice.title or "")).strip(" ._")[:40].rstrip(" ._")
    return f"{notice_key(notice)}_{title}"


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def won(value: float | None) -> str:
    return f"{value:,.0f}원" if value else "확인 필요"


def deadline_of(candidate) -> str:
    found = candidate.schedule.earliest
    return f"{found[1]:%Y-%m-%d %H:%M} ({found[0]})" if found else "일정 미상 — 공고문 확인"


def qualification_label(candidate) -> str:
    q = candidate.qualification
    if not q.checked:
        return "미확인 — 공고문 확인"
    return "자격 미달 가능" if q.missing_groups else "자격 충족"


def doc_label(f: Path, limit: int = 10) -> str:
    """파일 이름에서 "붙임4._"·"1." 같은 앞 번호를 뗀 이름 — 과업지시서·제안요청서·공고문이면 그 낱말."""
    for word in ("과업지시서", "제안요청서", "공고문"):
        if limit <= 10 and word[:2] in f.stem:
            return word
    name = re.sub(r"^[\s\d._\-]*(?:붙임|별첨|첨부)?[\s\d._\-]*", "", f.stem).replace("_", " ")
    return (re.sub(r"\s+", " ", _UNSAFE.sub(" ", name)).strip()[:limit].strip() or f.stem[:limit]).rstrip(" .")


def is_short_doc(hwpx: Path) -> bool:
    from nego.attachments import extract_text

    try:
        return len(extract_text(hwpx.read_bytes(), "hwpx")) <= WHOLE_FORM_CHARS
    except Exception:  # noqa: BLE001 — 못 읽으면 서식으로 안 봄
        return False


def build_forms(files: list[Path], company: dict, candidate, out: Path, work: Path) -> tuple[list[str], list[str], dict[Path, Path]]:
    """공고 문서의 서식을 서식별 파일로 자르고 회사 정보·공고 정보를 채운다(표본 규칙).
    돌려주는 값: (제출서류 체크리스트 줄, 자동 입력·안내 줄, {옛 한글 원본: 변환한 hwpx})."""
    from quantdocs.build import convert_hwp

    n = candidate.notice
    bid = candidate.schedule.bid_deadline
    bid_date = f"{bid:%Y-%m-%d}" if bid else None
    notes: list[str] = []
    converted: dict[Path, Path] = {}
    cuts = []
    whole_candidates: list[tuple[Path, Path]] = []
    for number, f in enumerate(files):
        suffix = f.suffix.lower()
        if suffix not in (".hwp", ".hwpx"):
            continue
        src = f
        if suffix == ".hwp":
            src, why = convert_hwp(f, work / "converted")
            if src is None:
                notes.append(f"{f.name}: 서식을 자르지 못함 — 옛 한글 변환 실패({why})")
                continue
            converted[f] = src
        cut_dir = work / "cut" / str(number)
        try:
            cut_dir.mkdir(parents=True)
            names, _ = split_forms(src, cut_dir, all_forms=True)  # 표로 거르지 않음 — 표의 번호가 실제 서식과 어긋나는 공고가 있어 서식이 빠짐(2026-11-05)
        except Exception as err:  # noqa: BLE001 — 문서 하나를 못 잘라도 나머지 자료는 만든다
            log.warning("서식 자르기 실패 %s: %s", f.name, err)
            notes.append(f"{f.name}: 서식을 자르지 못함({err})")
            continue
        if names:
            cuts.append((f, cut_dir, names))
        elif _WHOLE_FORM.search(f.stem) and not _NOT_WHOLE_FORM.search(f.stem) and is_short_doc(src):
            whole_candidates.append((f, src))
    out.mkdir(parents=True, exist_ok=True)
    checklist = ["제출서류 체크리스트 — 공고 문서 안의 서식을 모두 만들었습니다 (자동 생성)", "공고문의 제출서류 목록과 대조해 필요 없는 서식은 빼 주세요.", ""]
    used: set[str] = set()

    def target_for(name: str, f: Path) -> Path:
        """같은 번호가 이미 있으면 앞에 문서 이름을 붙임(표본의 과업지시서_서식01)."""
        number = name.split("_")[0]
        if number not in used:
            used.add(number)
            return _unique(out / name)
        return _unique(out / f"{doc_label(f)}_{name}")

    # 서식이 가장 많은 문서가 본 문서
    for f, cut_dir, names in sorted(cuts, key=lambda row: -len(row[2])):
        for name in names:
            target = target_for(name, f)
            filled = fill_form(cut_dir / name, target, company, bid_date, n.title, notice_key(n))
            checklist.append(f"[ ] {target.name}")
            notes.append(f"{target.name}: " + (", ".join(filled) if filled else "채운 칸 없음"))
    cut_files = {row[0] for row in cuts}
    for f, src in whole_candidates:  # 파일 전체가 서식인 첨부
        if f in cut_files:
            continue
        target = _unique(out / f"별도_{doc_label(f, 40)}.hwpx")
        shutil.copyfile(src, work / target.name)
        filled = fill_form(work / target.name, target, company, bid_date, n.title, notice_key(n))
        checklist.append(f"[ ] {target.name}")
        notes.append(f"{target.name}: " + (", ".join(filled) if filled else "채운 칸 없음"))
    stems = {f.stem for f in files if f.suffix.lower() in (".hwp", ".hwpx")}
    for number, f in enumerate(files):  # 같은 이름의 한글 파일이 있으면 한글 쪽을 씀 — PDF로만 있는 서식만
        if f.suffix.lower() != ".pdf" or f.stem in stems:
            continue
        cut_dir = work / "pdf" / str(number)
        try:
            names = split_pdf(f, cut_dir)
            for name in names:
                target = target_for(name, f)
                filled, why = fill_pdf(cut_dir / name, target, company, n.title)
                checklist.append(f"[ ] {target.name} — " + ("PDF 자동 입력, 칸 위치 확인 필요" if filled else "PDF 서식, 직접 작성"))
                notes.append(f"{target.name}: " + (", ".join(filled) if filled else why or "채운 칸 없음"))
        except Exception as err:  # noqa: BLE001 — PDF 하나를 못 나눠도 나머지 자료는 만든다
            log.warning("PDF 서식 나누기 실패 %s: %s", f.name, err)
            notes.append(f"{f.name}: PDF 서식을 나누지 못함({err})")
    return checklist, notes, converted


def read_docs(files: list[Path]) -> list[tuple[Path, str]]:
    """공고문·제안요청서·과업지시서의 글(읽지 못한 파일은 건너뜀)."""
    from nego.attachments import _sniff_ext, extract_text

    out = []
    for f in files:
        if f.suffix.lower() in (".hwp", ".hwpx", ".pdf") and _DOC_FILE.search(f.name):
            try:
                data = f.read_bytes()
                out.append((f, extract_text(data, f.suffix.lower().lstrip(".") or (_sniff_ext(data) or ""))))
            except Exception as err:  # noqa: BLE001 — 한 문서를 못 읽어도 나머지로 만든다
                log.warning("공고 문서 읽기 실패 %s: %s", f.name, err)
    return out


def _digits_date(value) -> str | None:
    """'2026-11-03 17:58:00'·'202611031758' → 20261103."""
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    return digits if len(digits) == 8 else None


def place_notice_files(files: list[Path], converted: dict[Path, Path], folder: Path, stamp: str) -> dict[Path, Path]:
    """공고 문서를 맨 위에 표본 이름(날짜_이름_v1)으로. 옛 한글은 변환한 hwpx도 나란히. {원본: 옮긴 파일}."""
    placed = {}
    for f in files:
        stem = re.sub(r"\s+", "_", f.stem)[:80].rstrip("_.")
        target = _unique(folder / f"{stamp}_{stem}_v1{f.suffix}")
        if f in converted:
            shutil.copyfile(converted[f], target.with_suffix(".hwpx"))
        os.replace(f, target)
        placed[f] = target
    return placed


def submit_deadline(schedule) -> str | None:
    """서류 판단 기준이 되는 제출 마감 — 공동수급협정·의견등록·게시·개찰 빼고 가장 늦은 마감."""
    days = [when.strftime("%Y-%m-%d") for name, when in schedule.all_deadlines
            if re.search(r"마감|제출|접수|등록", name) and not re.search(r"공동수급|의견|게시|개찰", name)]
    return max(days) if days else None


def merge_into(old: Path, new: Path, final: Path, previous: dict) -> None:
    """재공고 — 맨 위 문서와 예전 서식은 이전공고로(담당자가 고쳤을 수 있어 지우지 않음), 회사준비서류는 그대로 두고 새 자료를 맨 위에."""
    archive = old / PREV_DIR
    archive.mkdir(exist_ok=True)
    for name in previous["files"]:
        if (old / name).exists():
            os.replace(old / name, _unique(archive / name))
    if (old / FORMS_DIR).exists():
        os.replace(old / FORMS_DIR, _unique(archive / f"{FORMS_DIR}_{previous['key']}"))
    for item in new.iterdir():
        if (old / item.name).is_file():
            (old / item.name).unlink()
        os.replace(item, old / item.name)
    new.rmdir()
    os.replace(old, final)


def build_folder(candidate, amount, source, company: dict, out_root: Path, now: datetime, fetch: Callable | None = None,
                 previous: dict | None = None) -> tuple[Path, list[str]]:
    """임시 폴더에 다 만든 뒤 이름을 바꾼다 — 만들다 실패해도 반쪽 폴더가 담당자에게 안 보이게.
    `previous`(재공고)면 그 폴더에 합친다. 돌려주는 값: (폴더, 맨 위 공고 문서 이름들 — 다음 재공고 때 이것만 옮긴다)."""
    if fetch is None:
        from quantdocs.g2b import fetch_notice as fetch
    n = candidate.notice
    final = out_root / folder_name(n)
    old = out_root / previous["folder"] if previous else None
    tmp = out_root / f".작성중_{notice_key(n)}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        posted_digits = _digits_date(getattr(n, "posted_at", None))
        stamp = posted_digits or f"{now:%Y%m%d}"
        with tempfile.TemporaryDirectory(prefix="under100m_") as work_dir:
            notice_files = fetch(n.notice_no, tmp / ".받은 공고")
            files = list(notice_files.files)
            docs = read_docs(files)
            checklist, notes, converted = build_forms(files, company, candidate, tmp / FORMS_DIR, Path(work_dir))
            placed = place_notice_files(files, converted, tmp, stamp)
        shutil.rmtree(tmp / ".받은 공고", ignore_errors=True)
        notes += [f"첨부를 받지 못함: {s}" for s in notice_files.skipped]
        top_files = sorted(p.name for p in tmp.iterdir() if p.is_file())
        deadline = submit_deadline(candidate.schedule)
        posted = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}" if posted_digits else None
        form_names = [p.stem for p in (tmp / FORMS_DIR).iterdir() if p.suffix in (".hwpx", ".pdf")]
        keep_prep = bool(old and (old / PREP_DIR).exists())  # 담당자가 넣은 파일이 있어 재공고 때는 건드리지 않음
        need, ref = prepare(None if keep_prep else tmp / PREP_DIR, [text for _, text in docs], form_names, COMPANY_DOCS, deadline, posted, now.date(), company)
        if not write_summary_pdf(candidate, amount, source, tmp / SUMMARY, notice_key(n), need, ref, deadline, [placed[f].name for f, _ in docs], now):
            notes.append("한글 글꼴이 없어 요약 PDF를 만들지 못함")
        if keep_prep:
            notes.append(f"재공고: {PREP_DIR}는 그대로 둠 — 바뀐 제출서류가 있는지 공고문과 대조하세요.")
        if not form_names:
            notes.insert(0, "공고 문서에서 서식을 찾지 못함 — 공고문을 확인하세요.")
        (tmp / FORMS_DIR / FORMS_LIST).write_text(
            "\n".join(checklist + ["", f"공고 유형: {candidate.category}", "", "자동으로 채운 칸과 안내 — 제출 전 원문과 대조하세요."] + notes) + "\n",
            encoding="utf-8")
        if old:
            merge_into(old, tmp, final, previous)
        else:
            os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final, top_files


def alert_text(candidate, amount, source, folder: Path, updated: bool = False) -> str:
    n = candidate.notice
    head = f"재공고·정정 — 기존 폴더 갱신, 이전 문서는 {PREV_DIR}로" if updated else "새 공고 폴더"
    text = (f"[1억 미만 시험] {head} ({candidate.category})\n{n.title}\n공고번호: {notice_key(n)}\n{n.demand_institution or n.notice_institution}\n사업금액: {won(amount)} {source}\n"
            f"마감: {deadline_of(candidate)}\n자격: {qualification_label(candidate)}\n폴더: {folder.name}\n{n.detail_url or ''}")
    lines = issue_lines(COMPANY_DOCS, candidate.attachment_text)
    if lines:
        text += "\n\n새로 발급할 서류 (제출 요구 여부는 공고문에서 확인)\n" + "\n".join(lines)
    return text


def fetch_basis(notice, config) -> float | None:
    """기초금액 — 물품 등 조회가 안 되면 None(요약엔 공고문 금액이 대신 들어감)."""
    from nego import bidcalc, fields
    from nego.http_client import ApiError

    operation = bidcalc.BASIS_OPERATIONS.get(notice.work_type)
    if not operation:
        return None
    try:
        rows = bidcalc.make_client(config.api).fetch_all_pages(fields.BID_NOTICE_BASE_URL, operation, {"inqryDiv": "2", "bidNtceNo": notice.notice_no}, "요약 기초금액")
    except ApiError as err:
        log.warning("기초금액 조회 실패 %s: %s", notice.notice_no, err)
        return None
    row = bidcalc._latest(rows, notice.notice_no)
    return bidcalc.parse_basis(row).get("basis") if row else None


def title_key(notice) -> str:
    """재공고 표시·띄어쓰기를 뗀 공고명 — 번호가 바뀐 재공고를 알아보는 데 쓴다."""
    return re.sub(r"\s+", "", _RE_NOTICE.sub("", notice.title or ""))


def find_previous(done: dict, notice, now: datetime) -> dict | None:
    """같은 공고번호의 다른 차수, 또는 180일 안의 같은 공고명·기관 — 이 구조로 만든 폴더만(예전 폴더는 `files`가 없음)."""
    institution = notice.demand_institution or notice.notice_institution
    for key, record in done.items():
        if record.get("status") != "made" or "files" not in record or record.get("replaced_by"):
            continue
        same_no = key.rsplit("-", 1)[0] == notice.notice_no
        recent = (now - datetime.fromisoformat(record["at"])).days <= SAME_TITLE_DAYS
        if same_no or (recent and record.get("title") == title_key(notice) and record.get("institution") == institution):
            return {**record, "key": key}
    return None


def collect(now: datetime, days: int, out: Path) -> list:
    """입찰 공고를 받아 대상만 고르고 첨부를 읽는다. 지난 마감·지역 미달은 수집기와 같은 규칙으로 뺀다."""
    from nego import qualify
    from nego.attachments import save_attachment_texts
    from nego.config import load_config
    from nego.http_client import DataGoKrClient
    from nego.pipeline import drop_expired, drop_region_unmet, run
    from nego.scope import CATEGORY_BID
    from webapp.qualview import build as qual_build, held_lookup

    config = load_config()
    candidates, stats, _ = run(config, now, {CATEGORY_BID}, period=(now - timedelta(days=days), now))
    targets = [c for c in candidates if is_target(c)]
    log.info("입찰 후보 %d건 중 전시관·체험관 %d건", len(candidates), len(targets))
    if targets:
        with tempfile.TemporaryDirectory(prefix="under100m_text_") as tmp:
            save_attachment_texts(targets, Path(tmp), held_codes=qualify.load_held_codes(config.held_raw),
                                  held_code_names=qualify.load_held_code_names(config.held_raw), now=now,
                                  code_names=config.code_names, held_raw=config.held_raw,
                                  client=DataGoKrClient(config.api))
    open_targets = drop_region_unmet(drop_expired(targets, now, stats), stats)
    if len(open_targets) != len(targets):
        kept = {id(c) for c in open_targets}
        for c in targets:
            if id(c) not in kept:
                log.info("마감 지남·지역 미달로 뺌: %s %s", notice_key(c.notice), c.notice.title)
    for c in open_targets:  # 요약 PDF 칸 — 나라장터 기초금액, 자격 묶음
        c.basis = fetch_basis(c.notice, config)
        if c.qualification.checked:
            c.qual_parts = qual_build(c.qualification, held_lookup(config.held_raw), {"industry": True, "product": bool(c.attachment_text)})
    return open_targets


def run_once(out: Path, state_path: Path, days: int, now: datetime | None = None, *, collect_fn: Callable = collect,
             load_profile: Callable = lambda: load_company(COMPANY_PROFILE), fetch: Callable | None = None,
             notify: Callable[[str], object] = lambda text: None) -> dict:
    """한 번 돌린다. 처리한 공고는 기록에 남겨 다시 만들지 않고, 재공고·정정공고는 같은 폴더를 갱신한다."""
    now = now or datetime.now()
    state = load_state(state_path)
    done = state.setdefault("notices", {})
    result = {"made": [], "over": 0, "failed": []}
    todo = []
    for candidate in collect_fn(now, days, out):
        record = done.get(notice_key(candidate.notice), {})
        if record.get("status") in ("made", "over") or record.get("tries", 0) >= MAX_TRIES:
            continue
        amount, source = business_amount(candidate.notice, candidate.attachment_text)
        if amount is not None and amount >= LIMIT:
            done[notice_key(candidate.notice)] = {"status": "over", "amount": amount, "source": source,
                                                  "at": now.isoformat(timespec="seconds")}
            result["over"] += 1
            continue
        todo.append((candidate, amount, source))
    if todo:
        company = load_profile()
        out.mkdir(parents=True, exist_ok=True)
    for candidate, amount, source in sorted(todo, key=lambda row: notice_key(row[0].notice)):  # 같은 번호면 옛 차수부터 — 새 차수가 합쳐진다
        key = notice_key(candidate.notice)
        previous = find_previous(done, candidate.notice, now)
        if previous and not (out / previous["folder"]).is_dir():  # 담당자가 폴더를 지웠으면 새로
            previous = None
        try:
            folder, files = build_folder(candidate, amount, source, company, out, now, fetch, previous)
        except Exception as err:  # noqa: BLE001 — 한 공고 실패로 나머지를 막지 않는다. 다음 차례에 다시.
            tries = done.get(key, {}).get("tries", 0) + 1
            done[key] = {"status": "failed", "tries": tries, "error": str(err)[:300], "at": now.isoformat(timespec="seconds")}
            result["failed"].append(key)
            log.exception("폴더 생성 실패 %s (%d/%d회)", key, tries, MAX_TRIES)
            if tries >= MAX_TRIES:
                notify(f"[1억 미만 시험] 폴더 생성 {MAX_TRIES}회 실패 — 더 시도하지 않음\n{candidate.notice.title}\n{key}\n{err}")
            continue
        if previous:
            done[previous["key"]] = {**done[previous["key"]], "folder": folder.name, "replaced_by": key}
        done[key] = {"status": "made", "folder": folder.name, "amount": amount, "source": source, "files": files,
                     "title": title_key(candidate.notice), "institution": candidate.notice.demand_institution or candidate.notice.notice_institution,
                     "at": now.isoformat(timespec="seconds")}
        result["made"].append(folder.name)
        save_state(state_path, state)  # 알림이 실패해도 같은 폴더를 또 만들지 않게 먼저 저장
        notify(alert_text(candidate, amount, source, folder, updated=bool(previous)))
    save_state(state_path, state)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1억 미만 입찰(전시관·체험관) 공고 폴더 만들기")
    parser.add_argument("--out", default=os.environ.get("UNDER100M_OUT", ""), help="결과 폴더")
    parser.add_argument("--state", default=os.environ.get("UNDER100M_STATE", ""), help="처리 기록 파일")
    parser.add_argument("--days", type=int, default=3, help="최근 며칠 게시분을 볼지")
    parser.add_argument("--no-notify", action="store_true", help="텔레그램 알림 안 보냄")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%m-%d %H:%M")
    if not args.out:
        print("결과 폴더가 없습니다 — --out 또는 UNDER100M_OUT", file=sys.stderr)
        return 2
    out = Path(args.out)
    state_path = Path(args.state) if args.state else out.parent / "under100m_state.json"

    def notify(text: str) -> None:
        if args.no_notify:
            return
        from nego.telegram import send_dev_alert

        errors = send_dev_alert(text)
        if errors is None:
            log.warning("텔레그램 설정이 없어 알림을 보내지 못함(TELEGRAM_BOT_TOKEN·TELEGRAM_DEV_CHAT_ID)")
        elif errors:
            log.warning("텔레그램 알림 실패: %s", html.escape(str(errors))[:200])

    try:
        result = run_once(out, state_path, args.days, notify=notify)
    except Exception as err:  # noqa: BLE001 — 같은 오류를 1시간마다 알리지 않는다
        log.exception("실행 실패")
        state = load_state(state_path)
        message = str(err)[:300]
        if state.get("last_error") != message:
            state["last_error"] = message
            save_state(state_path, state)
            notify(f"[1억 미만 시험] 실행 실패\n{message}")
        return 1
    state = load_state(state_path)
    if state.pop("last_error", None) is not None:
        save_state(state_path, state)
    log.info("끝 — 새 폴더 %d건, 1억 이상 %d건, 실패 %d건", len(result["made"]), result["over"], len(result["failed"]))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
