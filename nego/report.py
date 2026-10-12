"""리포트 생성 — 콘솔 / CSV / HTML."""

from __future__ import annotations

import csv
import html
import re
from datetime import datetime
from pathlib import Path

from .pipeline import Candidate, RunStats
from .screen import parse_datetime

CSV_COLUMNS = [
    "검토순서",
    "잔여일수",
    "실질마감",
    "마감종류",
    "공고명",
    "발주기관",
    "업무구분",
    "추정가격",
    "신뢰도",
    "공고유형",
    "협상유형",
    "재공고",
    "해외의심",
    "공동수급",
    "자격판정",
    "참가가능지역",
    "첨부파일수",
    "공고번호",
    "차수",
    "원문URL",
]


def _fmt_money(value: float | None) -> str:
    if not value:
        return ""
    return f"{value:,.0f}원"


def _fmt_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _fmt_kr_date(value: datetime | None) -> str:
    """'2026. 8. 24.' 형태 — 기존 수집 시스템 리포트와 같은 날짜 표기."""
    return f"{value.year}. {value.month}. {value.day}." if value else ""


def _fmt_kr_datetime(value: datetime | None) -> str:
    """'2026. 8. 31. AM 12:01:13' 형태 — 기존 수집 시스템 리포트와 같은 표기."""
    if not value:
        return ""
    hour = value.hour % 12 or 12
    ampm = "AM" if value.hour < 12 else "PM"
    return f"{_fmt_kr_date(value)} {ampm} {hour}:{value.minute:02d}:{value.second:02d}"


def _fmt_kr_deadline(value: datetime | None) -> str:
    """'2026년 09월 20일 18시' 형태(24시간 기준) — 카드의 '마감/일정' 표시용."""
    return f"{value.year}년 {value.month:02d}월 {value.day:02d}일 {value.hour:02d}시" if value else ""


def _qualification_label(q) -> str:
    """CSV/콘솔용 짧은 표시. HTML 표는 이름표 대신 원(circle) 아이콘 +
    호버/포커스 팝업으로 보여준다 (`_qualification_cell_html` 참고) — 전체
    문장은 `q.summary`를 그대로 쓴다."""
    if not q.checked:
        return q.summary
    return "자격 충족" if q.missing_count == 0 else "자격 미달"


# 자격판정 원(circle) 안에 넣는 체크마크. 충족/미달 모두 같은 모양을 쓰고
# 색만 다르게 한다(파란 원=충족, 빨간 원=미달) — 표 안에서 이름표 문구 없이도
# 한눈에 훑을 수 있게 하기 위함이다.
_CHECK_SVG = (
    '<svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="#fff" stroke-width="2.4" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.8"/></svg>'
)

# 면허제한정보 API 필드가 실측상 "이름/코드"를 그대로 붙여 내려주는 경우가
# 있다(예: lcnsLmtNm="실내건축공사업/4990") — 첨부파일에서 뽑은 항목은 이미
# "이름(코드)" 형식이라 팝업 안에서 표기가 안 맞아 보인다. qualify.py의
# allowed_names 자체는 매칭(_is_group_satisfied)에 쓰이므로 이 변환은
# 표시 직전, 여기서만 한다.
_RAW_CODE_SUFFIX_RE = re.compile(r"^(.+)/([0-9]{4,10})$")


def _display_name(name: str) -> str:
    match = _RAW_CODE_SUFFIX_RE.match(name)
    if not match:
        return name
    label, code = match.group(1), match.group(2)
    return f"{label}({code})"


def _dedupe_names_preferring_code(names: list[str]) -> list[str]:
    """면허명 필드와 허용업종목록 필드가 같은 요건을 각각 내려주면(실측: 코드
    있는 "이름(코드)"와 코드 없는 "이름" 둘 다) 이름 기준으로 겹친다 —
    코드가 있는 쪽을 남긴다."""
    by_base: dict[str, str] = {}
    order: list[str] = []
    for n in names:
        # 맨 뒤 "(코드)"만 떼고 비교한다. 예전엔 첫 "(" 앞까지를 이름으로 봐서 "산업디자인전문회사(시각디자인분야)"·
        # "(환경디자인분야)"·"(종합디자인분야)"가 전부 같은 이름으로 합쳐져 "또는" 요건이 하나만 보였다(2026-09-30 제보).
        base = re.sub(r"\s*\(\d{4,10}\)\s*$", "", n)
        has_code = base != n
        if base not in by_base:
            order.append(base)
            by_base[base] = n
        elif has_code and re.sub(r"\s*\(\d{4,10}\)\s*$", "", by_base[base]) == by_base[base]:
            by_base[base] = n
    return [by_base[b] for b in order]


def _qualification_cell_html(q, esc) -> str:
    """표의 '자격판정' 칸. 이름표 문구 대신 원 아이콘 하나로 보여주고,
    미보유 자격이 있으면(빨간 원) 호버/포커스 시 이름·코드 목록을 팝업으로
    띄운다 — 여러 개일 때 칸 안에 태그를 줄줄이 나열하면 표가 지저분해지므로."""
    if not q.checked:
        return (
            '<button type="button" class="qual-dot" aria-label="자격정보 미확인 (통과)">'
            '<span class="circle circle-unchecked"></span>'
            '<span class="tip"><div class="tip-title" style="color:var(--unchecked-fg);">자격정보 미확인 (통과)</div>'
            "<div class=\"tip-note\">API·첨부파일 모두 판정 근거가 없어 fail-open으로 통과 처리됩니다.</div></span>"
            "</button>"
        )
    # 같은 미보유 자격이 여러 그룹에 걸쳐 잡히면(예: 첨부문서 내 같은 코드가
    # 참가자격 절에 여러 줄 등장) 그대로 중복 표시된다 — summary()와 동일하게
    # 이름 기준으로 중복 제거.
    miss_names = list(dict.fromkeys(_display_name(name) for g in q.missing_groups for name in g.allowed_names))
    miss_names = _dedupe_names_preferring_code(miss_names)
    if miss_names:
        items = "".join(f'<div class="tip-item">{esc(n)}</div>' for n in miss_names)
        label = f"자격 미달 — 미보유 {len(miss_names)}건: " + ", ".join(miss_names)
        return (
            f'<button type="button" class="qual-dot" aria-label="{esc(label)}">'
            f'<span class="circle circle-fail">{_CHECK_SVG}</span>'
            f'<span class="tip"><div class="tip-title" style="color:var(--fail-fg);">미보유 자격 {len(miss_names)}건</div>'
            f"{items}"
            '<div class="tip-note">이름 뒤 괄호 숫자는 세부품명번호·업종코드입니다. '
            "코드가 없는 항목은 면허제한정보 API에 코드 필드 자체가 없어 이름만 표시됩니다.</div></span>"
            "</button>"
        )
    # 미달 쪽과 같은 형식("이름(코드)")으로 충족된 자격도 보여준다. 직접
    # QualificationResult(...)를 만드는 옛 테스트처럼 satisfied_groups가 없는
    # 경우엔 목록 없이 "자격 충족"만 남긴다(하위 호환).
    ok_names = list(dict.fromkeys(_display_name(name) for g in q.satisfied_groups for name in g.allowed_names))
    ok_names = _dedupe_names_preferring_code(ok_names)
    if not ok_names:
        return (
            '<button type="button" class="qual-dot" aria-label="자격 충족">'
            f'<span class="circle circle-pass">{_CHECK_SVG}</span>'
            '<span class="tip"><div class="tip-title" style="color:var(--pass-fg);">자격 충족</div></span>'
            "</button>"
        )
    items = "".join(f'<div class="tip-item">{esc(n)}</div>' for n in ok_names)
    label = f"자격 충족 — 충족된 자격 {len(ok_names)}건: " + ", ".join(ok_names)
    return (
        f'<button type="button" class="qual-dot" aria-label="{esc(label)}">'
        f'<span class="circle circle-pass">{_CHECK_SVG}</span>'
        f'<span class="tip"><div class="tip-title" style="color:var(--pass-fg);">충족된 자격 {len(ok_names)}건</div>'
        f"{items}"
        '<div class="tip-note">이름 뒤 괄호 숫자는 세부품명번호·업종코드입니다. '
        "코드가 없는 항목은 면허제한정보 API에 코드 필드 자체가 없어 이름만 표시됩니다.</div></span>"
        "</button>"
    )


def _deadline_cell_html(c: Candidate, esc) -> str:
    """표의 '입찰마감일' 칸. 잔여일수를 'D-N' 배지로 먼저 보여주고, 그 아래
    실제 마감 시각과 어떤 마감(자격등록/공동수급협정/입찰/첨부파일 제출기한)
    인지를 작게 붙인다."""
    earliest = c.schedule.earliest
    if earliest is None:
        return '<span class="dim nowrap">일정 미상</span>'
    label, when = earliest
    days = c.days_left
    if days is None:
        dday_html = ""
    elif days < 0:
        dday_html = '<div class="dday-closed">공고 마감</div>'
    else:
        dday_html = f'<div class="dday">{esc(f"D-{days}")}</div>'
    return (
        f"{dday_html}"
        f'<div class="dl-date">{esc(_fmt_kr_deadline(when))}</div>'
        f'<div class="dl-label dim">{esc(label)}</div>'
    )


def _opening_cell_html(c: Candidate, esc) -> str:
    """표의 '공고일/개찰일' 칸. 둘 다 연월일까지만 표기한다(시각은 마감일
    칸에서 이미 다루므로 여기선 생략)."""
    posted = _fmt_kr_date(parse_datetime(c.notice.posted_at))
    opening = _fmt_kr_date(parse_datetime(c.notice.opening_at))
    # 헤더가 이미 "공고일 / 개찰일" 순서를 알려주므로 칸 안에서는 값만 두 줄로
    # 보여준다(둘째 줄은 옅은 색으로 개찰일임을 구분) — 라벨을 더 붙이면 좁은
    # 칸에서 한 줄로 이어 붙이기 어려워진다.
    return f'<div class="nowrap">{esc(posted) or "미상"}</div><div class="dim nowrap">{esc(opening) or "일정 미상"}</div>'


def _row(index: int, c: Candidate) -> dict[str, str]:
    earliest = c.schedule.earliest
    return {
        "검토순서": str(index),
        "잔여일수": "" if c.days_left is None else f"D-{c.days_left}" if c.days_left >= 0 else "마감",
        "실질마감": _fmt_dt(earliest[1]) if earliest else "",
        "마감종류": earliest[0] if earliest else "일정 미상",
        "공고명": c.notice.title,
        "발주기관": c.notice.notice_institution or "",
        "업무구분": c.notice.work_type,
        "추정가격": _fmt_money(c.notice.budget),
        "신뢰도": c.screen_result.confidence or "",
        "공고유형": c.category or "",
        "협상유형": c.variant or "",
        "재공고": "Y" if c.is_re_notice else "",
        "해외의심": "🌐 몽골" if c.screen_result.overseas_flag else "",
        "공동수급": c.joint.label,
        "자격판정": c.qualification.summary,
        "참가가능지역": ", ".join(c.regions),
        "첨부파일수": str(len(c.notice.attachments)),
        "공고번호": c.notice.notice_no,
        "차수": c.notice.notice_ord,
        "원문URL": c.notice.detail_url or "",
    }


def write_csv(candidates: list[Candidate], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for i, candidate in enumerate(candidates, start=1):
            writer.writerow(_row(i, candidate))
    return path


def report_title(stats: RunStats) -> str:
    """리포트/발송 메시지 제목. 매일 실행(입찰)과 주간 실행(협상·규격가격)을 구분한다."""
    kinds = " · ".join(stats.categories) if stats.categories else "전체 유형"
    return f"나라장터 입찰 모니터링 리포트 ({kinds})"


def render_console(candidates: list[Candidate], stats: RunStats) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"{report_title(stats)} — 검토 후보")
    lines.append("=" * 78)
    lines.append(
        f"수집 {stats.fetched}건 → 대상 유형 {stats.in_scope}건 → 업역 {stats.screened_in}건 → 후보 {stats.candidates}건"
    )
    other = f" / 다른 유형 {stats.other_category}" if stats.other_category else ""
    lines.append(
        f"  (취소공고 제외 {stats.cancelled} / 수의계약 {stats.private_contract}{other} / "
        f"이전 차수 {stats.old_ordinal})"
    )
    if stats.qualification_flagged:
        lines.append(f"  후보 중 자격 미달 표시 {stats.qualification_flagged}건 (제외하지 않음 — 공동수급 확인)")
    if stats.screened_out:
        detail = " · ".join(f"{k} {v}" for k, v in sorted(stats.screened_out.items()))
        lines.append(f"  업역 스크리닝 제외: {detail}")
    if stats.failed_operations:
        lines.append(f"  ⚠ 조회 실패: {', '.join(stats.failed_operations)}")
    if stats.license_error:
        lines.append("  ⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용 (fail-open)")
    if stats.prespec_requested:
        # 위 "수집 N건"은 본공고만 센 값이다. 사전규격은 대상 유형부터 함께 센다.
        lines.append(f"  사전규격 {stats.prespec_fetched}건 함께 수집 (본공고와 같은 필터 적용)")
        if stats.prespec_error:
            lines.append(f"  ⚠ 사전규격 조회 실패 (본공고는 정상): {stats.prespec_error}")
    lines.append("")

    if not candidates:
        lines.append("조건에 맞는 공고가 없습니다.")
        return "\n".join(lines)

    for i, c in enumerate(candidates, start=1):
        flags = []
        if c.is_re_notice:
            flags.append("재공고")
        if c.notice.bid_method and "직찰" in c.notice.bid_method:
            flags.append("직찰(비전자)")
        if c.screen_result.overseas_flag:
            flags.append(f"🌐 해외개최 의심(몽골) — 직접 확인 필요 ({c.screen_result.overseas_evidence})")
        linked = c.notice.linked_system
        if linked:
            flags.append(f"{linked['name']} 공고 — 규격서·상세는 {linked['url']}")

        earliest = c.schedule.earliest
        deadline = ("일정 미상" if c.days_left is None else "마감됨" if c.days_left < 0 else f"D-{c.days_left}")
        deadline_kind = f" ({earliest[0]})" if earliest else ""

        lines.append(f"[{i:2d}] [{c.category or '유형 미상'}] {c.notice.title}")
        lines.append(
            f"     {deadline}{deadline_kind}  ·  {c.notice.notice_institution or '발주기관 미상'}"
            f"  ·  {_fmt_money(c.notice.budget) or '금액 미상'}"
        )
        lines.append(
            f"     {c.screen_result.confidence}"
            f"  ·  공동수급: {c.joint.label}  ·  {c.qualification.summary}"
        )
        if flags:
            lines.append(f"     {' · '.join(flags)}")
        if c.qualification.missing_groups:
            names = c.qualification.missing_groups[0].allowed_names[:3]
            lines.append(f"     미충족 그룹 예: {', '.join(names)}")
        lines.append("")

    return "\n".join(lines)


_HTML_HEAD = """<meta charset="utf-8">
<title>나라장터 입찰 모니터링 리포트</title>
<style>
  :root {
    --bg:#fbfbfa; --fg:#1f1e1c; --muted:#6b6a66; --line:#e3e1dc; --accent:#2554c7;
    --pass-bg:#eaf2fb; --pass-bd:#a8c7e6; --pass-fg:#1d5a99;
    --fail-bg:#fdeeea; --fail-bd:#e6b8ae; --fail-fg:#9a3412;
    --unchecked-bg:#fbf3d9; --unchecked-bd:#e3cf8f; --unchecked-fg:#8a6d16;
  }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif; line-height:1.55; }
  .wrap { max-width:1320px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:1.45rem; margin:0 0 6px; letter-spacing:-0.01em; }
  .sub { color:var(--muted); font-size:0.85rem; margin-bottom:20px; }
  .warn { color:#9a3412; font-size:0.85rem; display:block; margin-bottom:16px; }
  .section { font-size:1.05rem; font-weight:700; margin:28px 0 14px;
             padding-bottom:8px; border-bottom:2px solid var(--fg); }
  .tablewrap { overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:#fff; }
  table { border-collapse:collapse; width:100%; min-width:820px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
           vertical-align:top; font-size:0.75rem; line-height:1.4; }
  /* 셀 내용을 항상 블록(div)으로 감싸서 시작점을 맞춘다 — 뱃지/버튼 같은
     인라인 요소가 셀에 바로 있으면 브라우저가 셀마다 다른 줄상자 높이를
     잡아 계약방법/예가방법/수요기관/개찰일 같은 옆 칸과 첫 줄이 미묘하게
     어긋나 보였다. */
  td > div:first-child { margin-top:0; }
  thead th { background:#f3f2ee; color:var(--muted); font-weight:700; font-size:0.72rem;
             letter-spacing:0.01em; border-bottom:2px solid var(--fg); white-space:nowrap; }
  tbody tr:hover { background:#f6f5f2; }
  .badges { display:flex; flex-direction:column; align-items:flex-start; gap:4px; }
  .badge { font-size:0.68rem; padding:2px 8px; border-radius:6px; font-weight:600;
           border:1px solid var(--line); background:#f6f5f2; color:var(--muted);
           display:inline-block; white-space:nowrap; line-height:1.3; }
  .badge.confidence-strong { border-color:#bfd8c4; background:#eef6f0; color:#2f6b45; }
  .badge.category { border-color:#c9d4e6; background:#eef2f9; color:#34507a; }
  .badge.overseas { border-color:#e6b8ae; background:#fdeeea; color:#9a3412; cursor:help;
                    position:relative; margin-left:4px; }
  /* 위치는 JS(_TOOLTIP_POSITION_SCRIPT)가 호버/포커스 시점에 top/left를
     직접 계산해서 인라인으로 넣는다 — 표의 어느 행에 있든(맨 위/맨 아래/
     맨 오른쪽 칸) 뷰포트 밖으로 잘리지 않게 하려고. position:fixed라
     스크롤 컨테이너에 안 걸리고 뷰포트 좌표를 그대로 쓸 수 있다. */
  .badge.overseas .tip {
    visibility:hidden; opacity:0; pointer-events:none;
    position:fixed; top:0; left:0; width:230px;
    background:#fff; border:1px solid var(--line); border-radius:10px;
    box-shadow:0 6px 20px rgba(0,0,0,0.14); padding:10px 12px;
    font-size:0.74rem; font-weight:400; color:var(--fg); text-align:left;
    line-height:1.5; transition:opacity .12s ease; z-index:20;
  }
  .badge.overseas:hover .tip, .badge.overseas:focus-visible .tip { visibility:visible; opacity:1; }
  .badge.overseas .tip .tip-row { color:var(--muted); margin-bottom:6px; }
  .badge.overseas .tip .tip-row b { color:var(--fg); font-weight:600; }
  .badge.overseas .tip .tip-tag {
    display:inline-block; padding:3px 8px; border-radius:999px;
    background:#eef6f0; border:1px solid #bfd8c4; color:#2f6b45; font-size:0.68rem;
  }
  .rebadge { font-size:0.65rem; padding:1px 6px; border-radius:4px; font-weight:700;
             background:var(--fail-bg); color:var(--fail-fg); border:1px solid var(--fail-bd);
             margin-left:4px; white-space:nowrap; }
  .notice-no { font-size:0.68rem; color:var(--muted); white-space:nowrap; }
  .notice-title { font-weight:600; }
  .notice-title a { color:var(--accent); text-decoration:none; }
  .notice-title a:hover { text-decoration:underline; }
  .title-text { display:block; max-width:175px; overflow:hidden;
                text-overflow:ellipsis; white-space:nowrap; }
  .kwtags { margin-top:6px; display:flex; flex-wrap:wrap; gap:4px; }
  .kwtag { font-size:0.65rem; padding:2px 7px; border-radius:6px;
           border:1px solid var(--line); background:#f6f5f2; color:var(--muted); }
  .dim { color:var(--muted); }
  .nowrap { white-space:nowrap; }
  .dday { display:inline-block; font-weight:800; color:#fff; background:#c0392b;
          font-size:0.68rem; padding:1px 6px; border-radius:4px; letter-spacing:0.01em; }
  .dday-closed { display:inline-block; font-weight:800; color:#fff; background:#4a4a4a;
                 font-size:0.68rem; padding:1px 6px; border-radius:4px; letter-spacing:0.01em; }
  .dl-date { white-space:nowrap; margin-top:4px; font-size:0.72rem; }
  .dl-label { font-size:0.65rem; margin-top:1px; white-space:nowrap; }

  /* 자격판정: 원 아이콘 + 호버/포커스 팝업 (미보유 자격명·코드번호) */
  .qual-dot { position:relative; display:inline-flex; align-items:center; justify-content:center;
              width:26px; height:26px; padding:0; margin:0; border:none; background:none; cursor:help; }
  .qual-dot .circle { width:16px; height:16px; border-radius:50%; display:flex;
                       align-items:center; justify-content:center; }
  .circle-fail { background:#c0392b; }
  .circle-pass { background:#2554c7; }
  .circle-unchecked { background:#e3cf8f; }
  /* 위/아래로 고정해서 열면(둘 다 실측으로 확인됨) 표의 반대쪽 끝 행에서
     뷰포트 밖으로 잘린다 — 원 오른쪽(공간이 없으면 왼쪽)에, 세로로는
     버튼 위치를 기준으로 뷰포트 안에 들어오게 JS가 top/left를 직접
     계산해서 연다. position:fixed라 스크롤 컨테이너에 안 걸린다. */
  .qual-dot .tip {
    visibility:hidden; opacity:0; pointer-events:none;
    position:fixed; top:0; left:0;
    width:230px; background:#fff; border:1px solid var(--line); border-radius:10px;
    box-shadow:0 8px 22px rgba(0,0,0,0.16); padding:10px 12px;
    font-size:0.72rem; font-weight:400; color:var(--fg); text-align:left; line-height:1.5;
    transition:opacity .12s ease; z-index:30;
  }
  .qual-dot:hover .tip, .qual-dot:focus-visible .tip { visibility:visible; opacity:1; }
  .qual-dot .tip .tip-title { font-weight:700; margin-bottom:6px; }
  .qual-dot .tip .tip-item { padding:4px 2px; border-top:1px solid var(--line); word-break:break-all; }
  .qual-dot .tip .tip-item:first-child { border-top:none; }
  .qual-dot .tip .tip-note { color:var(--muted); font-size:0.65rem; margin-top:6px; }

  td.center { text-align:center; }

  .empty { text-align:center; color:var(--muted); padding:48px 0; }
  @media (max-width:520px) { .wrap { padding:20px 16px 48px; } }
</style>
"""

# 자격판정 원(qual-dot)과 해외의심 배지 팁의 위치를 위/아래로만 고정해서
# 열면(둘 다 실측으로 확인됨) 표의 반대쪽 끝 행에서 뷰포트 밖으로 잘린다.
# 그래서 원 오른쪽으로 열되(공간이 없으면 왼쪽), 세로 위치도 버튼을 기준
# 삼아 뷰포트 안에 들어오도록 클램프한다 — 팝업을 position:fixed로 두고
# 호버/포커스 시점에 top/left를 인라인으로 직접 계산해 넣는다.
# visibility:hidden 상태에서도 레이아웃은 잡혀 있어(display:none이 아니므로)
# 호버/포커스가 들어오는 시점에 getBoundingClientRect로 크기를 미리 잴 수
# 있다.
_TOOLTIP_FLIP_SCRIPT = """<script>
(function () {
  var GAP = 8, EDGE = 8;
  function place(el) {
    var tip = el.querySelector('.tip');
    if (!tip) return;
    var elRect = el.getBoundingClientRect();
    var tipRect = tip.getBoundingClientRect();
    var vw = window.innerWidth, vh = window.innerHeight;

    var left = elRect.right + GAP;
    if (left + tipRect.width + EDGE > vw) {
      left = elRect.left - tipRect.width - GAP;  // 오른쪽에 공간이 없으면 왼쪽으로
    }
    left = Math.max(EDGE, Math.min(left, vw - tipRect.width - EDGE));

    var top = elRect.top + elRect.height / 2 - tipRect.height / 2;  // 버튼 세로 중앙에 맞춘 뒤
    top = Math.max(EDGE, Math.min(top, vh - tipRect.height - EDGE));  // 뷰포트 안으로 클램프

    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  document.querySelectorAll('.qual-dot, .badge.overseas').forEach(function (el) {
    el.addEventListener('mouseenter', function () { place(el); });
    el.addEventListener('focus', function () { place(el); });
  });
})();
</script>"""


def render_html(candidates: list[Candidate], stats: RunStats, generated_at: datetime) -> str:
    def esc(text: str | None) -> str:
        return html.escape(str(text or ""))

    parts = [_HTML_HEAD, '<div class="wrap">']
    parts.append(f"<h1>{esc(report_title(stats))}</h1>")

    period = ""
    if stats.period_begin and stats.period_end:
        period = f"조회 기간: {_fmt_kr_date(stats.period_begin)} ~ {_fmt_kr_date(stats.period_end)} · "
    parts.append(f'<div class="sub">{period}생성 시각: {_fmt_kr_datetime(generated_at)}</div>')

    if stats.failed_operations:
        parts.append(f'<span class="warn">⚠ 조회 실패: {esc(", ".join(stats.failed_operations))}</span>')
    if stats.license_error:
        parts.append('<span class="warn">⚠ 면허제한정보 조회 실패 → 자격 게이트 미적용(fail-open)</span>')

    parts.append(f'<div class="section">본공고 ({len(candidates)}건)</div>')

    if not candidates:
        parts.append('<div class="empty">조건에 맞는 공고가 없습니다.</div>')
    else:
        parts.append('<div class="tablewrap"><table>')
        parts.append(
            "<thead><tr>"
            "<th>분야</th><th>계약방법</th><th>공고번호 / 공고명</th>"
            "<th>추정가격(원) / 배정예산(원)</th><th>자격판정</th>"
            "<th>공동수급(컨소시엄)</th><th>예가방법</th><th>수요기관</th>"
            "<th>공고일 / 개찰일</th><th>입찰마감일</th>"
            "</tr></thead><tbody>"
        )
        for c in candidates:
            title_text = esc(c.notice.title)
            title = title_text
            if c.notice.detail_url:
                title = f'<a href="{esc(c.notice.detail_url)}" target="_blank" rel="noopener">{title}</a>'
            # 제목이 길면 한 줄로 잘라 "..."으로 표시한다(공고번호/공고명 칸에
            # 그 밑으로 키워드 태그 등이 이어지는데, 제목이 여러 줄로 감싸이면
            # 그 아래 칸들과 줄맞춤이 깨져 보였다) — title 속성으로 전체 제목은
            # 마우스오버 시 그대로 보여준다.
            title = f'<div class="title-text" title="{title_text}">{title}</div>'

            confidence_cls = " confidence-strong" if c.screen_result.confidence == "강력추천" else ""
            re_badge = '<span class="rebadge">재공고</span>' if c.is_re_notice else ""
            linked = c.notice.linked_system
            if linked:
                re_badge += (
                    f'<a class="rebadge" href="{esc(linked["url"])}" target="_blank" rel="noopener" '
                    f'title="나라장터엔 첨부가 없습니다 — 규격서·상세는 {esc(linked["name"])}에서 확인">'
                    f'{esc(linked["name"])}에서 확인</a>'
                )

            overseas_badge = ""
            if c.screen_result.overseas_flag:
                overseas_badge = (
                    '<span class="badge overseas" tabindex="0">ⓘ 해외의심'
                    '<span class="tip">'
                    f'<div class="tip-row"><b>발주기관:</b> {esc(c.notice.notice_institution) or "미상"}</div>'
                    f'<span class="tip-tag">{esc(c.screen_result.overseas_evidence)}</span>'
                    "</span>"
                    "</span>"
                )

            kwtags = ""
            if c.screen_result.matched_keywords:
                tags = "".join(f'<span class="kwtag">키워드:{esc(k)}</span>' for k in c.screen_result.matched_keywords)
                kwtags = f'<div class="kwtags">{tags}</div>'

            # 헤더가 "추정가격(원) / 배정예산(원)" 순서를 알려주므로 칸 안 라벨은
            # "추정"/"배정" 두 글자로 줄인다 — 값 하나만 있을 때도 어느 쪽인지는
            # 알 수 있게 유지하되 폭은 아낀다.
            money_parts = []
            if c.notice.estimated_price:
                money_parts.append(f'<div class="nowrap">추정 {esc(_fmt_money(c.notice.estimated_price))}</div>')
            if c.notice.assigned_budget and c.notice.assigned_budget != c.notice.estimated_price:
                money_parts.append(
                    f'<div class="dim nowrap">배정 {esc(_fmt_money(c.notice.assigned_budget))}</div>'
                )
            money_html = "".join(money_parts) or '<span class="dim">미상</span>'

            parts.append(
                "<tr>"
                '<td><div class="badges">'
                f'<span class="badge category">{esc(c.category or "유형 미상")}</span>'
                f'<span class="badge{confidence_cls}">{esc(c.screen_result.confidence)}</span>'
                f'<span class="badge">{esc(c.notice.work_type)}</span></div></td>'
                f'<td><div class="nowrap">{esc(c.notice.contract_method) or "-"}</div></td>'
                f'<td class="notice-title">'
                f'<div class="notice-no">{esc(c.notice.notice_no)}{re_badge}{overseas_badge}</div>'
                f"{title}{kwtags}</td>"
                f"<td><div>{money_html}</div></td>"
                f'<td class="center"><div>{_qualification_cell_html(c.qualification, esc)}</div></td>'
                f'<td><div class="nowrap">{esc(c.joint.label)}</div></td>'
                f'<td><div class="nowrap">{esc(c.notice.estimate_price_method) or "-"}</div></td>'
                f'<td><div class="nowrap">{esc(c.notice.demand_institution) or "-"}</div></td>'
                f'<td class="center"><div>{_opening_cell_html(c, esc)}</div></td>'
                f"<td><div>{_deadline_cell_html(c, esc)}</div></td>"
                "</tr>"
            )
        parts.append("</tbody></table></div>")

    parts.append("</div>")
    parts.append(_TOOLTIP_FLIP_SCRIPT)
    return "\n".join(parts)


def save_reports(
    candidates: list[Candidate], stats: RunStats, output_dir: Path, generated_at: datetime
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%d_%H%M")

    prefix = "_".join(stats.categories) if stats.categories else "전체"
    csv_path = write_csv(candidates, output_dir / f"공고_{prefix}_{stamp}.csv")
    html_path = output_dir / f"공고_{prefix}_{stamp}.html"
    html_path.write_text(render_html(candidates, stats, generated_at), encoding="utf-8")

    return {"csv": csv_path, "html": html_path}
