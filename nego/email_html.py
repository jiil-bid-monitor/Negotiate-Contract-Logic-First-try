"""주간 메일 본문 HTML — 공고마다 카드 한 장, 미보유 자격이 있으면 오른쪽에 '자격 미보유' 상자 (2026-10-20).

텔레그램 첨부 HTML(`report.render_html`)은 브라우저에서 여는 파일이라 `<style>`·팝업(마우스를 올리면 뜨는 미보유 자격 목록)을
쓴다. 지메일 등은 메일 본문의 `<style>`을 지워서, 숨겨 둔 팝업 내용이 모든 줄에 회색 상자로 펼쳐지고 동그라미 아이콘도
사라졌다(10/20 테스트 메일로 확인). 그래서 메일 본문은 팀원 레포 주간 메일과 같은 카드 모양으로 따로 그린다(사용자 결정):
- 스타일은 칸마다 직접 쓰고(`style="…"`), 팝업·스크립트·SVG를 쓰지 않는다. 카드 좌우 배치는 표(table)로
- 미보유 자격은 웹앱 자격 팝업과 같은 자료(`webapp/qualview.build`)로 필수 보유 / N개 중 1개 이상 / 방법 중 하나 / 조건부를 ✓·✗·–로
- 메일은 본문 + 텔레그램과 같은 HTML 첨부(`cli._send_email`)
"""

from __future__ import annotations

import html
from datetime import datetime
from functools import lru_cache

from .pipeline import Candidate, RunStats
from .report import _fmt_kr_date, _fmt_kr_datetime, _fmt_kr_deadline, _fmt_money, report_title

INK, DIM, LINE = "#1f2937", "#6b7280", "#e5e7eb"
LINK = "#1d4ed8"
PILL = "display:inline-block;padding:2px 8px;margin:0 4px 4px 0;border-radius:999px;font-size:11px;"
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
    return f'<div style="{style}padding:1px 0;">{mark} {_esc(label)}</div>'


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


def qualification_box(c: Candidate) -> str:
    """미보유 자격이 있는 공고만 '자격 미보유' 상자. 없으면 빈 문자열."""
    q = c.qualification
    if not q.checked or not q.missing_count:
        return ""
    from webapp.qualview import build

    has_text = bool((getattr(c, "attachment_text", "") or "").strip())
    parts = build(q, _held_lookup(), {"industry": c.notice.kind == "본공고" or has_text, "product": has_text})
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
            f'font-size:12px;line-height:1.5;color:#374151;">'
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


def _card(c: Candidate) -> str:
    n, sr = c.notice, c.screen_result
    pills = f'<span style="{PILL}background:#e5e7eb;color:#374151;font-weight:700;">{_esc(sr.confidence)}</span>'
    pills += f'<span style="color:{DIM};font-size:12px;">[{_esc(n.work_type)}]</span>'
    for flag, text in ((c.is_re_notice, "재공고"), (sr.overseas_flag, "해외의심")):
        if flag:
            pills += f' <span style="{PILL}background:#fef3c7;color:#b45309;">{text}</span>'
    title = _esc(n.title)
    if n.detail_url:
        title = f'<a href="{_esc(n.detail_url)}" style="color:{LINK};text-decoration:none;">{title}</a>'
    money = f"{_fmt_money(n.budget)}원" if n.budget else "금액 모름"
    rows = [("기관", n.demand_institution or n.notice_institution or "-"), ("사업금액", money),
            ("마감/일정", _deadline(c)), ("공고번호", n.notice_no), ("낙찰방법", n.award_method or "-"),
            ("공동수급", c.joint.label)]
    sync = getattr(c, "sync", None) or {}
    if sync.get("score") is not None:
        best = (sync.get("top") or [{}])[0]
        rows.append(("싱크로율", f'{round(sync["score"] * 100)}%' + (f' · 유사: {best.get("year") or ""} {best.get("title")}' if best.get("title") else "")))
    if not c.qualification.checked:
        rows.append(("자격", "확인 못 함 — 원문 확인"))
    info = "".join(f'<div style="margin:2px 0;">{k}: {_esc(v)}</div>' for k, v in rows)
    tags = ([f"품목:{p}" for p in sr.matched_product_codes or []] if getattr(sr, "section", None) == "세부품명" else []) \
        + [f"키워드:{k}" for k in sr.matched_keywords or []]
    tag_html = "".join(f'<span style="{PILL}background:#eef2ff;color:#4338ca;">{_esc(t)}</span>' for t in tags)
    left = (f'<div>{pills}</div>'
            f'<div style="font-size:16px;font-weight:700;margin:6px 0 8px;">{title}</div>'
            f'<div style="font-size:13px;color:#374151;line-height:1.6;">{info}</div>'
            + (f'<div style="margin-top:8px;">{tag_html}</div>' if tag_html else ""))
    box = qualification_box(c)
    right = f'<td style="width:42%;vertical-align:top;padding:16px 16px 16px 0;">{box}</td>' if box else ""
    return (f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:separate;border:1px solid {LINE};'
            f'border-radius:10px;margin:0 0 12px;"><tr>'
            f'<td style="vertical-align:top;padding:16px 20px;">{left}</td>{right}</tr></table>')


def _section(title: str, rows: list[Candidate]) -> str:
    head = (f'<h3 style="margin:20px 0 10px;padding-bottom:6px;border-bottom:2px solid {INK};font-size:16px;color:{INK};">'
            f"{_esc(title)} ({len(rows)}건)</h3>")
    if not rows:
        return head + f'<p style="color:{DIM};font-size:13px;">조건에 맞는 공고가 없습니다.</p>'
    return head + "".join(_card(c) for c in rows)


def render_email_html(candidates: list[Candidate], stats: RunStats, generated_at: datetime) -> str:
    period = ""
    if stats.period_begin and stats.period_end:
        period = f"조회 기간: {_fmt_kr_date(stats.period_begin)} ~ {_fmt_kr_date(stats.period_end)} · "
    parts = [
        f'<div style="font-family:\'Malgun Gothic\',\'Apple SD Gothic Neo\',sans-serif;color:{INK};max-width:960px;">',
        f'<h2 style="margin:0 0 4px;font-size:20px;">{_esc(report_title(stats))}</h2>',
        f'<div style="color:{DIM};font-size:12px;">{period}생성 시각: {_esc(_fmt_kr_datetime(generated_at))}</div>',
        f'<div style="color:{DIM};font-size:12px;">표로 한눈에 보려면 첨부한 HTML 파일(텔레그램과 같은 리포트)을 여세요.</div>',
    ]
    if stats.failed_operations:
        parts.append(f'<p style="color:#b91c1c;font-size:13px;">⚠ 조회 실패: {_esc(", ".join(stats.failed_operations))}</p>')
    if stats.license_error:
        parts.append('<p style="color:#b91c1c;font-size:13px;">⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용(fail-open)</p>')
    interest = [c for c in candidates if getattr(c.screen_result, "section", "관심") != "세부품명"]
    product_only = [c for c in candidates if getattr(c.screen_result, "section", "관심") == "세부품명"]
    parts.append(_section("관심 공고", interest))
    if product_only:
        parts.append(_section("세부품명 공고 (키워드 없이 보유 품목으로 수집)", product_only))
    parts.append("</div>")
    return "\n".join(parts)
