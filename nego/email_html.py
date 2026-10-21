"""주간 메일 본문 HTML — 공고마다 카드 한 장, 미보유 자격이 있으면 오른쪽에 '자격 미보유' 상자 (2026-10-20).

텔레그램 첨부 HTML(`report.render_html`)은 브라우저에서 여는 파일이라 `<style>`·팝업(마우스를 올리면 뜨는 미보유 자격 목록)을
쓴다. 지메일 등은 메일 본문의 `<style>`을 지워서, 숨겨 둔 팝업 내용이 모든 줄에 회색 상자로 펼쳐지고 동그라미 아이콘도
사라졌다(10/20 테스트 메일로 확인). 그래서 메일 본문은 팀원 레포 주간 메일과 같은 카드 모양으로 따로 그린다(사용자 결정):
- 스타일은 칸마다 직접 쓰고(`style="…"`), 팝업·스크립트·SVG를 쓰지 않는다
- (10/21 사용자 요청) 가운데 정렬·확대/축소해도 같은 모양: 바깥은 가운데 표 하나(최대 760px), 카드 안 공고 정보 | 자격 미보유는
  inline-block 두 칸 — 넓으면 나란히(자격 상자는 오른쪽 끝), 확대해 좁아지면 위아래로. flex는 메일마다 지원이 달라 안 씀.
  글자 크기·줄 간격은 px로 고정, 칸 너비는 %가 아니라 최소·최대 너비로
- 미보유 자격은 웹앱 자격 팝업과 같은 자료(`webapp/qualview.build`)로 필수 보유 / N개 중 1개 이상 / 방법 중 하나 / 조건부를 ✓·✗·–로
- 메일은 본문 + 텔레그램과 같은 HTML 첨부(`cli._send_email`)
"""

from __future__ import annotations

import html
import uuid
from datetime import datetime
from functools import lru_cache

from .pipeline import Candidate, RunStats
from .report import (_fmt_kr_date, _fmt_kr_datetime, _fmt_kr_deadline, _fmt_money, is_prespec, report_title,
                     split_prespec, unchecked_reason)

INK, DIM, LINE = "#1f2937", "#6b7280", "#e5e7eb"
LINK = "#1d4ed8"
PAGE_BG = "#f3f4f6"
MAX_WIDTH = 1000  # 본문 최대 너비(px) — 넓은 화면에선 가운데, 좁으면 화면 너비에 맞춰 줄어듦 (10/21 760 → 1000, 사용자: 여백이 많음)
_BOX_WIDTH = 340  # 자격 미보유 상자 너비
# 정보 칸 최대 너비 — 카드 안쪽(MAX_WIDTH − 테두리 2 − 좌우 여백 40) − 상자 − 2(어긋남 여유). 둘이 나란히면 상자가 오른쪽 끝
_INFO_WIDTH = MAX_WIDTH - 42 - _BOX_WIDTH - 2
FONT = "font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;"
WRAP = "word-break:keep-all;overflow-wrap:break-word;"  # 한글은 낱말 단위로 줄바꿈, 긴 번호·주소만 중간에서
_BASE = "font-size:13px;line-height:20px;"  # 본문 글자 — 바깥 틀에서 물려받고, 두 칸 배치에서 font-size:0을 되돌릴 때만 다시 씀
PILL = "display:inline-block;padding:2px 8px;margin:0 4px 4px 0;border-radius:9px;font-size:11px;line-height:16px;"
# 지메일은 본문이 약 102KB를 넘으면 "[메일 내용 잘림]"으로 접는다(10/21 시험 메일 — 57건 180KB). 그래서 글자 크기·색·줄바꿈은
# 바깥 틀에 한 번만 두고 물려받게 하고, 칸마다 붙는 스타일은 최소로(모양은 그대로, 크기만 줄임)
_OK = "color:#15803d;"
_MISS = "color:#b91c1c;font-weight:600;"
_SPARE = "color:#9ca3af;"
_OR = '<div style="color:#9ca3af;font-size:11px;padding:1px 0 1px 16px;">또는</div>'


def _esc(text) -> str:
    return html.escape(str(text or ""))


# ── 자격 미보유 상자 ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _held_lookup() -> tuple:
    """보유 자격(config/held_qualifications.json) — 웹앱 팝업과 같은 판정 자료. 메일을 만들 때 한 번만 읽는다."""
    from webapp.qualview import held_lookup  # webapp → nego 순환 import를 피해 여기서

    from .config import load_config

    return held_lookup(load_config().held_raw)


def _line(label: str, held: bool, spare: bool) -> str:
    style, mark = (_OK, "✓") if held else (_SPARE, "–") if spare else (_MISS, "✗")
    return f'<div style="{style}">{mark} {_esc(label)}</div>'


def _block(heading: str, lines: list[str], joiner: str = "") -> str:
    return (f'<div style="margin-top:8px;"><div style="color:{DIM};margin-bottom:1px;">{heading}</div>'
            f"{joiner.join(lines)}</div>")


def _req_blocks(reqs: list[dict]) -> list[str]:
    """요건 목록 → 묶음 블록 (웹앱 index.html reqBlocksHtml과 같은 규칙). 필수 보유는 한 칸에 미보유 먼저."""
    blocks: list[str] = []
    must = [r["items"][0] for r in reqs if not r.get("any_of") and not r.get("combos") and not r.get("why") and r.get("items")]
    must = sorted(dict((i["label"], i) for i in must).values(), key=lambda i: i["held"])
    if must:
        head = "필수 보유" if len(must) == 1 else f"필수 보유 — {len(must)}개 모두 필요"
        blocks.append(_block(head, [_line(i["label"], i["held"], False) for i in must]))
    for r in reqs:
        if r.get("combos"):
            done = any(c["held"] for c in r["combos"])
            lines = [_line(" + ".join(x["label"] for x in c["rows"]), c["held"], done) for c in r["combos"]]
            blocks.append(_block(f"아래 방법 중 하나 — {'보유로 충족' if done else '미보유'}", lines, _OR))
        elif r.get("any_of"):
            items = list(dict((i["label"], i) for i in r["items"]).values())
            done = any(i["held"] for i in items)
            lines = [_line(i["label"], i["held"], done) for i in items]
            blocks.append(_block(f"아래 {len(items)}개 중 1개 이상 — {'보유로 충족' if done else '미보유'}", lines, _OR))
        elif r.get("why") and r.get("items"):
            # 조건부(없으면 공동수급) 요건은 없어도 "–", 첨부에서 찾은 "보유 확인"은 ✓
            i = r["items"][0]
            blocks.append(_block(_esc(r["why"]), [_line(i["label"], i["held"], bool(r.get("joint")))]))
    return blocks


def qualification_parts(c: Candidate) -> list[dict]:
    """웹앱 자격 팝업과 같은 부문별 요건 자료(`webapp/qualview.build`) — 메일 상자·텔레그램 HTML 팝업이 같이 쓴다."""
    from webapp.qualview import build

    has_text = bool((getattr(c, "attachment_text", "") or "").strip())
    return build(c.qualification, _held_lookup(), {"industry": c.notice.kind == "본공고" or has_text, "product": has_text})


def qualification_box(c: Candidate) -> str:
    """미보유 자격이 있는 공고만 '자격 미보유' 상자. 없으면 빈 문자열."""
    q = c.qualification
    if not q.checked or not q.missing_count:
        return ""
    parts = qualification_parts(c)
    sections = []
    for p in parts:
        blocks = _req_blocks(list(p.get("missing") or []) + list(p.get("satisfied") or [])) if p.get("status") == "미달" else []
        if blocks:
            # 자격 종류가 하나면 종류 이름은 생략(카드가 길어지지 않게)
            sections.append((f'<div style="margin-top:8px;font-weight:700;color:#374151;">{_esc(p["name"])}</div>'
                             if len(parts) > 1 else "") + "".join(blocks))
    if not sections:
        return ""
    # 공동수급 불허면 부족한 자격을 함께 채울 업체를 구할 수 없어 빨강, 허용이면 노랑, 정보 없으면 원문 확인
    denied = c.joint.raw_value is not None and not c.joint.allowed
    joint = ("공동수급 여부는 원문 확인" if c.joint.raw_value is None
             else "공동수급 불허 — 참가 어려움" if denied else "공동수급으로 참가 검토")
    bg, border, fg = ("#fef2f2", "#fecaca", "#b91c1c") if denied else ("#fffbeb", "#fde68a", "#b45309")
    return (f'<div style="background:{bg};border:1px solid {border};border-radius:8px;padding:12px 14px;'
            f'font-size:12px;line-height:19px;">'
            f'<div style="font-size:13px;font-weight:700;color:{fg};">자격 미보유</div>'
            f'<div style="color:{DIM};">{_esc(joint)}</div>' + "".join(sections) + "</div>")


# ── 카드 ────────────────────────────────────────────────────────────

def _deadline(c: Candidate) -> str:
    earliest = c.schedule.earliest
    if earliest is None:
        return "일정 미상"
    label, when = earliest
    days = c.days_left
    dday = "" if days is None else " (마감)" if days < 0 else f" (D-{days})"
    return f"{_fmt_kr_deadline(when)} {label}{dday}"


def _info_table(rows: list[tuple[str, str]]) -> str:
    """이름표 | 값 두 칸 표 — 값이 길어 줄이 바뀌어도 값끼리 왼쪽 끝이 맞는다. 칸 여백은 표 속성 cellpadding으로(크기 줄이기)."""
    cells = "".join(f'<tr valign="top"><td width="72"><font color="{DIM}">{k}</font></td><td>{_esc(v)}</td></tr>'
                    for k, v in rows)
    return f'<table role="presentation" cellpadding="2" cellspacing="0" style="margin-left:-2px">{cells}</table>'


def _card(c: Candidate) -> str:
    n, sr = c.notice, c.screen_result
    pills = f'<span style="{PILL}background:#e5e7eb;color:#374151;font-weight:700;">{_esc(sr.confidence)}</span>'
    pills += f'<span style="{PILL}background:#f3f4f6;color:{DIM};">{_esc(n.work_type)}</span>'
    for flag, text in ((c.is_re_notice, "재공고"), (sr.overseas_flag, "해외의심")):
        if flag:
            pills += f'<span style="{PILL}background:#fef3c7;color:#b45309;">{text}</span>'
    title = _esc(n.title)
    if n.detail_url:
        title = f'<a href="{_esc(n.detail_url)}" style="color:{LINK};text-decoration:none;">{title}</a>'
    money = _fmt_money(n.budget) or "금액 모름"  # _fmt_money가 "원"까지 붙임(예전엔 "원원"으로 나왔음)
    rows = [("기관", n.demand_institution or n.notice_institution or "-"), ("사업금액", money),
            ("마감/일정", _deadline(c)), ("공고번호", n.notice_no), ("낙찰방법", n.award_method or "-"),
            ("공동수급", c.joint.label)]
    sync = getattr(c, "sync", None) or {}
    if sync.get("score") is not None:
        best = (sync.get("top") or [{}])[0]
        rows.append(("싱크로율", f'{round(sync["score"] * 100)}%' + (f' · 유사: {best.get("year") or ""} {best.get("title")}' if best.get("title") else "")))
    if not c.qualification.checked:
        rows.append(("자격", "사전규격 — 면허제한 정보 없음, 원문 확인" if is_prespec(c) else unchecked_reason(c)))
    tags = ([f"품목:{p}" for p in sr.matched_product_codes or []] if getattr(sr, "section", None) == "세부품명" else []) \
        + [f"키워드:{k}" for k in sr.matched_keywords or []]
    tag_html = f'<span style="{PILL}background:#eef2ff;color:#4338ca;">{_esc(" · ".join(tags))}</span>' if tags else ""
    left = (f'<div>{pills}</div>'
            f'<div style="font-size:16px;line-height:24px;font-weight:700;margin:2px 0 8px;">{title}</div>'
            + _info_table(rows)
            + (f'<div style="margin-top:8px;">{tag_html}</div>' if tag_html else ""))
    box = qualification_box(c)
    if box:
        # 넓으면 나란히(정보 | 자격 상자는 카드 오른쪽 끝), 좁아지면(확대·휴대폰) 자격 상자가 아래로.
        # flex는 메일 프로그램마다 늘이기를 지워(10/21 실제 메일 — 칸이 글자 길이만큼만 잡혀 오른쪽이 빔) 쓰지 않고,
        # 거의 모든 메일이 지키는 inline-block + 최대 너비로: 두 칸 최대 너비 합이 카드 안쪽 너비와 같아
        # 나란히 놓이면 상자가 오른쪽 끝에 붙는다. 바깥 font-size:0은 두 칸 사이 빈칸을 없애려고
        body = (f'<div style="font-size:0;line-height:0;">'
                f'<div style="display:inline-block;vertical-align:top;width:100%;max-width:{_INFO_WIDTH}px;'
                f'padding:0 16px 12px 0;box-sizing:border-box;{_BASE}">{left}</div>'
                f'<div style="display:inline-block;vertical-align:top;width:100%;max-width:{_BOX_WIDTH}px;'
                f'box-sizing:border-box;{_BASE}">{box}</div></div>')
    else:
        body = left
    return (f'<div style="background:#fff;border:1px solid {LINE};border-radius:12px;padding:18px 20px;'
            f'margin-bottom:14px">{body}</div>\n')


def _section(title: str, rows: list[Candidate]) -> str:
    head = (f'<h3 style="margin:28px 0 12px;padding-bottom:8px;border-bottom:2px solid {INK};'
            f'font-size:17px;line-height:24px;color:{INK};">{_esc(title)} ({len(rows)}건)</h3>')
    if not rows:
        return head + f'<p style="color:{DIM};font-size:13px;line-height:20px;">조건에 맞는 공고가 없습니다.</p>'
    body = ""
    for sub, sub_rows in split_prespec(rows):  # 본공고·사전규격이 섞이면 소구역으로
        if sub:
            body += (f'<div style="margin:18px 0 10px;font-size:14px;line-height:20px;font-weight:700;color:{DIM};">'
                     f"{_esc(sub)} ({len(sub_rows)}건)</div>")
        body += "".join(_card(c) for c in sub_rows)
    return head + body


INTEREST, PRODUCT = "관심 공고", "세부품명 공고"
_PRODUCT_TITLE = "세부품명 공고 (키워드 없이 보유 품목으로 수집)"


def split_parts(candidates: list[Candidate]) -> list[tuple[str, list[Candidate]]]:
    """메일을 나눠 보낼 구역 — 관심 공고(늘), 세부품명 공고(있을 때만). 10/21 사용자 요청: 한 통이 길어 뒤가 잘려서."""
    interest = [c for c in candidates if getattr(c.screen_result, "section", "관심") != "세부품명"]
    product_only = [c for c in candidates if getattr(c.screen_result, "section", "관심") == "세부품명"]
    return [(INTEREST, interest)] + ([(PRODUCT, product_only)] if product_only else [])


def render_email_html(candidates: list[Candidate], stats: RunStats, generated_at: datetime, part: str | None = None,
                      attached: bool = True) -> str:
    """메일 본문. part가 INTEREST·PRODUCT면 그 구역만(메일 나눠 보내기), None이면 두 구역 다.
    attached=False면(첨부 없는 두 번째 통) '첨부 파일을 여세요' 안내를 뺀다."""
    period = ""
    if stats.period_begin and stats.period_end:
        period = f"조회 기간: {_fmt_kr_date(stats.period_begin)} ~ {_fmt_kr_date(stats.period_end)} · "
    meta = f"color:{DIM};font-size:12px;line-height:18px;"
    alert = "color:#b91c1c;font-size:13px;line-height:20px;margin-top:8px;"
    heading = report_title(stats) + (f" — {part}" if part else "")
    header = (f'<div style="background:#fff;border:1px solid {LINE};border-radius:12px;padding:20px 22px;">'
              f'<h2 style="margin:0 0 6px;font-size:20px;line-height:28px;color:{INK};">{_esc(heading)}</h2>'
              f'<div style="{meta}">{period}생성 시각: {_esc(_fmt_kr_datetime(generated_at))}</div>'
              + (f'<div style="{meta}">표로 한눈에 보려면 첨부한 HTML 파일(텔레그램과 같은 리포트)을 여세요.</div>'
                 if attached else ""))
    if stats.failed_operations:
        header += f'<div style="{alert}">⚠ 조회 실패: {_esc(", ".join(stats.failed_operations))}</div>'
    if stats.license_error:
        header += f'<div style="{alert}">⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용(fail-open)</div>'
    header += "</div>"
    content = header
    for name, rows in split_parts(candidates):
        if part in (None, name):
            content += _section(_PRODUCT_TITLE if name == PRODUCT else name, rows)
    # 바깥 표로 가운데 정렬 — div의 margin:auto를 무시하는 메일 프로그램도 표의 align은 지킨다
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="width:100%;background:{PAGE_BG};border-collapse:collapse;">\n'
            f'<tr><td align="center" style="padding:24px 12px;">\n'
            f'<div style="max-width:{MAX_WIDTH}px;margin:0 auto;text-align:left;{FONT}color:#374151;{_BASE}{WRAP}">\n'
            f"{content}\n</div>\n</td></tr>\n</table>{_unique_tail()}")


def _unique_tail() -> str:
    """메일마다 다른, 보이지 않는 끝 글자. 지메일은 같은 대화(제목)의 앞 메일과 끝부분이 같으면 '•••'로 접는데
    (10/21 시험 메일 — 사용자: 접지 말 것), 끝이 매번 다르면 접지 않는다."""
    return (f'<div style="display:none;max-height:0;overflow:hidden;font-size:1px;line-height:1px;color:{PAGE_BG};'
            f'mso-hide:all;">{uuid.uuid4().hex}</div>')
