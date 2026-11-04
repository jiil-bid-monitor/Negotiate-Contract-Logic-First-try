"""1억 미만 입찰(전시관·체험관) 공고 폴더 자동 생성 — 최소판 (2026-11-04 사용자 결정).

새 공고마다 결과 폴더에 공고문·요약 PDF·서식을 넣고 개발자 방에 알린다. 회사 자료는 잠금 묶음에서만 읽어
이 코드엔 회사 정보가 없다. 평가 점수·투찰 금액·회사 서류 판정은 하지 않는다.

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

log = logging.getLogger("under100m")

TARGET_WORDS = ("전시관", "체험관")
LIMIT = 100_000_000
MAX_TRIES = 3
# 고정 회사 정보만 채운다. 자본금·면허·연혁·실적처럼 공고마다 확인할 값은 비운다(10/28 사용자 결정).
COMPANY_KEYS = frozenset({"회사명", "대표자", "사업자등록번호", "법인등록번호", "주소", "전화번호", "팩스번호",
                          "홈페이지", "이메일", "설립일"})
TITLE_LABELS = ("입찰건명", "건명", "사업명", "용역명", "과업명", "공사명", "계약건명", "입찰명", "공고명")
DATE_LABELS = ("입찰일자", "입찰일", "입찰서제출일")
_AMOUNT = re.compile(r"(기초\s*금액|소요\s*예산|사업\s*예산|사\s*업\s*비|사업\s*금액)"
                     r"[^0-9\n]{0,30}?(?:금\s*)?([0-9]{1,3}(?:,[0-9]{3})+)\s*(천원|원)?")
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]')


def is_target(candidate) -> bool:
    """본공고 후보 중 공고명에 전시관·체험관이 있는 입찰(띄어쓰기 무시)."""
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


def folder_name(notice, now: datetime) -> str:
    title = re.sub(r"\s+", " ", _UNSAFE.sub("_", notice.title or "")).strip(" ._")[:40].rstrip(" ._")
    return f"{now:%m%d}_{notice_key(notice)}_{title}"


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


def missing_names(candidate) -> list[str]:
    names = []
    for group in candidate.qualification.missing_groups:
        if group.combos:
            names.append(" 또는 ".join(" + ".join("/".join(row) for row in combo) for combo in group.combos))
        else:
            names.append(" 또는 ".join(group.allowed_names))
    return list(dict.fromkeys(n for n in names if n))


def qualification_label(candidate) -> str:
    q = candidate.qualification
    if not q.checked:
        return "미확인 — 공고문 확인"
    return "자격 미달 가능" if q.missing_groups else "자격 충족"


def write_summary_pdf(candidate, amount: float | None, source: str, path: Path) -> bool:
    """공고 요약 PDF(규칙으로만). 한글 글꼴이 없으면 False — 나머지 자료는 그대로 만든다."""
    from xml.sax.saxutils import escape

    from quantdocs.bundle import _FONTS
    from nego.text_requirements import summarize_flag
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = next((f for f in _FONTS if Path(f).exists()), None)
    if not font:
        return False
    pdfmetrics.registerFont(TTFont("KR", font, subfontIndex=0) if font.endswith(".ttc") else TTFont("KR", font))
    body = ParagraphStyle("b", fontName="KR", fontSize=9.5, leading=14, wordWrap="CJK")
    head = ParagraphStyle("h", fontName="KR", fontSize=11.5, leading=17, spaceBefore=9, spaceAfter=4)
    title = ParagraphStyle("t", fontName="KR", fontSize=14, leading=20, spaceAfter=4, wordWrap="CJK")

    def para(text) -> Paragraph:
        return Paragraph(escape(str(text or "-")).replace("\n", "<br/>"), body)

    def table(rows: list[list[str]]) -> Table:
        t = Table([[para(a), para(b)] for a, b in rows], colWidths=[32 * mm, 138 * mm])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, "#b8c0bc"), ("BACKGROUND", (0, 0), (0, -1), "#eef2f0"),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    n = candidate.notice
    amount_text = f"{won(amount)} ({source})" if amount else "확인 필요 — 공고문·나라장터에서 금액을 찾지 못함"
    story = [Paragraph(escape(n.title or ""), title), para("자동 생성 자료입니다. 공고문 원문을 기준으로 확인하세요."), Spacer(1, 6),
             table([["공고번호", notice_key(n)], ["수요기관", n.demand_institution or n.notice_institution], ["공고기관", n.notice_institution],
                    ["사업금액", amount_text], ["원문", n.detail_url or "-"]])]
    story += [Paragraph("일정", head),
              table([[name, f"{when:%Y-%m-%d %H:%M}"] for name, when in candidate.schedule.all_deadlines]
                    or [["일정", "일정 미상 — 공고문 확인"]])]
    missing = missing_names(candidate)
    region = candidate.region_check
    story += [Paragraph("참가 조건", head),
              table([["자격 판정", qualification_label(candidate)], ["미보유 자격", "\n".join(missing) or "-"],
                     ["지역", f"{region.status} ({', '.join(region.required)})" if region.required else region.status],
                     ["공동수급", candidate.joint.label]])]
    for flag in candidate.text_flags:
        story += [Paragraph(f"확인 필요 — {escape(str(flag.get('kind', '')))}", head),
                  table([[k, v] for k, v in summarize_flag(flag)] + [["원문", flag.get("text", "")]])]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                      bottomMargin=18 * mm, title=n.title or "공고 요약").build(story)
    return True


def build_forms(files: list[Path], vault, notice_values: list, out: Path, work: Path) -> list[str]:
    """공고 문서의 서식을 서식별 파일로 — 회사 고정 정보·사업명·입찰일자만 채움, 인력·실적 등은 양식 그대로. 안내 줄을 돌려준다."""
    from quantdocs.build import convert_hwp, is_manual
    from quantdocs.fill import fill_hwpx
    from quantdocs.forms_cut import cut

    notes = []
    for f in files:
        suffix = f.suffix.lower()
        if suffix not in (".hwp", ".hwpx"):
            continue
        src = f
        if suffix == ".hwp":
            src, why = convert_hwp(f, work / "converted")
            if src is None:
                notes.append(f"{f.name}: 서식을 자르지 못함 — 옛 한글 변환 실패({why})")
                continue
        try:
            cut_dir = work / "cut" / f.stem
            for form in cut(src, cut_dir):
                if is_manual(form.title):
                    (out / "서식_직접기입").mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(cut_dir / form.file, out / "서식_직접기입" / form.file)
                    continue
                report = fill_hwpx(cut_dir / form.file, out / "서식" / form.file, vault, None, keys=COMPANY_KEYS,
                                   history=False, extras=notice_values)
                notes.append(f"{form.file}: " + (", ".join(report.filled) or "채운 칸 없음"))
        except Exception as err:  # noqa: BLE001 — 문서 하나를 못 잘라도 나머지 자료는 만든다
            log.warning("서식 자르기 실패 %s: %s", f.name, err)
            notes.append(f"{f.name}: 서식을 자르지 못함({err})")
    return notes


def build_folder(candidate, amount, source, vault, out_root: Path, now: datetime, fetch: Callable | None = None) -> Path:
    """임시 폴더에 다 만든 뒤 이름을 바꾼다 — 만들다 실패해도 반쪽 폴더가 담당자에게 안 보이게."""
    if fetch is None:
        from quantdocs.g2b import fetch_notice as fetch
    n = candidate.notice
    final = out_root / folder_name(n, now)
    tmp = out_root / f".작성중_{notice_key(n)}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        with tempfile.TemporaryDirectory(prefix="under100m_") as work_dir:
            work = Path(work_dir)
            notice_files = fetch(n.notice_no, tmp / "공고문")
            bid = candidate.schedule.bid_deadline
            when: date | None = bid.date() if bid else None
            values = [(TITLE_LABELS, n.title or "")]
            if when:
                values.append((DATE_LABELS, f"{when.year}년 {when.month}월 {when.day}일"))
            notes = build_forms(list(notice_files.files), vault, values, tmp, work)
        notes += [f"첨부를 받지 못함: {s}" for s in notice_files.skipped]
        if not write_summary_pdf(candidate, amount, source, tmp / "공고정보_요약.pdf"):
            notes.append("한글 글꼴이 없어 요약 PDF를 만들지 못함")
        (tmp / "자동입력_확인.txt").write_text(
            "자동으로 채운 칸과 안내입니다. 제출 전 원문과 대조하세요.\r\n\r\n" + "\r\n".join(notes) + "\r\n", encoding="utf-8")
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def alert_text(candidate, amount, source, folder: Path) -> str:
    n = candidate.notice
    return (f"[1억 미만 시험] 새 공고 폴더\n{n.title}\n{n.demand_institution or n.notice_institution}\n사업금액: {won(amount)} {source}\n"
            f"마감: {deadline_of(candidate)}\n자격: {qualification_label(candidate)}\n폴더: {folder.name}\n{n.detail_url or ''}")


def collect(now: datetime, days: int, out: Path) -> list:
    """입찰 공고를 받아 대상만 고르고 첨부를 읽는다. 지난 마감·지역 미달은 수집기와 같은 규칙으로 뺀다."""
    from nego import qualify
    from nego.attachments import save_attachment_texts
    from nego.config import load_config
    from nego.http_client import DataGoKrClient
    from nego.pipeline import drop_expired, drop_region_unmet, run
    from nego.scope import CATEGORY_BID

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
    return open_targets


def run_once(out: Path, state_path: Path, days: int, now: datetime | None = None, *, collect_fn: Callable = collect,
             load_vault: Callable | None = None, fetch: Callable | None = None,
             notify: Callable[[str], object] = lambda text: None) -> dict:
    """한 번 돌린다. 처리한 공고는 기록에 남겨 다시 만들지 않고, 정정공고(차수 변경)는 새 폴더로 만든다."""
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
        if load_vault is None:
            from quantdocs.vault import load as load_vault
        vault = load_vault()
        out.mkdir(parents=True, exist_ok=True)
    for candidate, amount, source in todo:
        key = notice_key(candidate.notice)
        try:
            folder = build_folder(candidate, amount, source, vault, out, now, fetch)
        except Exception as err:  # noqa: BLE001 — 한 공고 실패로 나머지를 막지 않는다. 다음 차례에 다시.
            tries = done.get(key, {}).get("tries", 0) + 1
            done[key] = {"status": "failed", "tries": tries, "error": str(err)[:300], "at": now.isoformat(timespec="seconds")}
            result["failed"].append(key)
            log.exception("폴더 생성 실패 %s (%d/%d회)", key, tries, MAX_TRIES)
            if tries >= MAX_TRIES:
                notify(f"[1억 미만 시험] 폴더 생성 {MAX_TRIES}회 실패 — 더 시도하지 않음\n{candidate.notice.title}\n{key}\n{err}")
            continue
        done[key] = {"status": "made", "folder": folder.name, "amount": amount, "source": source,
                     "at": now.isoformat(timespec="seconds")}
        result["made"].append(folder.name)
        save_state(state_path, state)  # 알림이 실패해도 같은 폴더를 또 만들지 않게 먼저 저장
        notify(alert_text(candidate, amount, source, folder))
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
