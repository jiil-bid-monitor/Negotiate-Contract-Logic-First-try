"""공고정보_요약.pdf — 표본(1억미만_사업폴더_취합_20261028)과 같은 양식. 규칙으로만, AI 없음."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape as esc

LOGO = Path(__file__).resolve().parent / "jiil_logo.png"
LIMIT = 100_000_000
KIND_NAME = {"실적": "실적 요건", "인력": "기술인력 요건", "건축사사무소": "건축사사무소 요건", "업종": "업종 요건"}
_AMOUNT = re.compile(r"(추정\s*가격|기초\s*금액|배정\s*예산|소요\s*예산|사업\s*예산|사\s*업\s*비|사업\s*금액|예정\s*금액)"
                     r"[^0-9\n]{0,30}?(?:금\s*)?([0-9]{1,3}(?:,[0-9]{3})+)\s*(천원|원)?")


def won(value: float | None) -> str:
    if not value:
        return "-"
    return f"{value:,.0f}원" + (f" ({value / 1e8:.2f}억)" if value >= 1e7 else "")


def money_rows(candidate, amount: float | None, source: str) -> list[tuple[str, str]]:
    n = candidate.notice
    raw = getattr(n, "raw", None) or {}
    rate = raw.get("sucsfbidLwltRate")
    from nego.models import PLACEHOLDER_AMOUNT

    def api(value: float | None) -> str:  # 1원 같은 자리 표시 값 = 발주처가 금액을 안 올림
        return f"비공개 (나라장터에 {value:,.0f}원으로 등록)" if value and value < PLACEHOLDER_AMOUNT else won(value)

    hidden = any(v and v < PLACEHOLDER_AMOUNT for v in (n.estimated_price, n.assigned_budget))
    rows = [("추정가격", api(n.estimated_price)), ("배정예산", api(n.assigned_budget)),
            ("기초금액", won(getattr(candidate, "basis", None))), ("낙찰하한율", f"{rate}%" if rate else "-"),
            ("예가방법", getattr(n, "estimate_price_method", None) or "-")]
    seen = set()
    for m in _AMOUNT.finditer(candidate.attachment_text or ""):  # 공고문 금액 문장(종류별 첫 값) — 칸이 비었으면 그 칸에, 없는 종류면 새 줄
        value = float(m.group(2).replace(",", "")) * (1000 if m.group(3) == "천원" else 1)
        kind = re.sub(r"\s", "", m.group(1))
        if value < 1_000_000 or kind in seen:
            continue
        seen.add(kind)
        hit = next((i for i, row in enumerate(rows) if row[0] == kind), None)
        if hit is None:
            rows.append((kind, won(value) + " — 공고문"))
        elif rows[hit][1] == "-":
            rows[hit] = (kind, won(value) + " — 공고문")
    if amount:
        rows.insert(0, ("사업금액", f"{won(amount)} — {source} · {'1억 미만' if amount < LIMIT else '1억 이상'}"))
    elif hidden:
        rows.insert(0, ("사업금액", "비공개 — 나라장터 금액이 자리 표시 값이고 공고문에도 없음, 발주처·공고문 확인"))
    return rows


def license_labels(qualification) -> list[str]:
    from nego.qualify import _row_label

    groups = list(qualification.missing_groups) + list(qualification.satisfied_groups)
    return list(dict.fromkeys(_row_label(row) for group in groups for row in (group.rows or [])))


def req_lines(reqs: list[dict], mark: dict) -> list[tuple[str, list[str]]]:
    """웹앱 자격 팝업과 같은 묶음: 필수 보유 / N개 중 1개 이상 / 방법 중 하나."""
    out = []
    must = [r["items"][0] for r in reqs if not r.get("any_of") and not r.get("combos") and not r.get("why") and r.get("items")]
    if must:
        out.append(("필수 보유", [f"{mark[i['held']]} {esc(i['label'])}" for i in must]))
    for r in reqs:
        if r.get("combos"):
            done = any(c["held"] for c in r["combos"])
            out.append(("아래 방법 중 하나", [f"{mark[c['held'] if c['held'] or not done else None]} {esc(' + '.join(x['label'] for x in c['rows']))}"
                                         for c in r["combos"]]))
        elif r.get("any_of"):
            done = any(i["held"] for i in r["items"])
            out.append((f"아래 {len(r['items'])}개 중 1개 이상", [f"{mark[i['held'] if i['held'] or not done else None]} {esc(i['label'])}" for i in r["items"]]))
        elif r.get("why") and r.get("items"):
            item = r["items"][0]
            out.append((esc(r["why"]), [f"{mark[item['held'] if item['held'] else None]} {esc(item['label'])}"]))
    return out


def _kit(now: datetime):
    """요약 PDF 공통(글꼴·글 모양·표·쪽 띠). 한글 글꼴이 없으면 None."""
    from quantdocs.bundle import _FONTS
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = next((f for f in _FONTS if Path(f).exists()), None)
    if not font:
        return None
    bold = next((b for b in (font.replace("malgun.ttf", "malgunbd.ttf"), font.replace("NanumGothic.ttf", "NanumGothicBold.ttf"))
                 if b != font and Path(b).exists()), font)  # 굵은 글꼴이 없으면(NAS) 보통 글꼴로
    pdfmetrics.registerFont(TTFont("KR", font, subfontIndex=0) if font.endswith(".ttc") else TTFont("KR", font))
    pdfmetrics.registerFont(TTFont("KRB", bold, subfontIndex=0) if bold.endswith(".ttc") else TTFont("KRB", bold))
    pdfmetrics.registerFontFamily("KR", normal="KR", bold="KRB", italic="KR", boldItalic="KRB")
    base = ParagraphStyle("b", fontName="KR", fontSize=8.5, leading=11.5, wordWrap="CJK")
    small = ParagraphStyle("s", parent=base, fontSize=7.5, leading=10, textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=base, fontName="KRB", fontSize=14, leading=19)
    h2 = ParagraphStyle("h2", parent=base, fontName="KRB", fontSize=10.5, leading=14, spaceBefore=6, spaceAfter=2,
                        textColor=colors.HexColor("#1b6e43"))
    def table(rows) -> Table:
        data = [[Paragraph(esc(str(k)), base), Paragraph(esc(str(v if v not in (None, "") else "-")), base)] for k, v in rows]
        t = Table(data, colWidths=[32 * mm, 140 * mm])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
                               ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f2")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
        return t

    def frame(canvas, doc) -> None:
        """쪽마다 위 로고·제목 띠, 아래 회사명·작성일·쪽 번호."""
        w, h = A4
        canvas.saveState()
        if LOGO.exists():
            canvas.drawImage(str(LOGO), 18 * mm, h - 16 * mm, width=17 * mm, height=7.6 * mm, mask="auto", preserveAspectRatio=True)
        canvas.setFont("KRB", 9)
        canvas.setFillColor(colors.HexColor("#17291f"))
        canvas.drawRightString(w - 18 * mm, h - 13 * mm, "공고정보 요약")
        canvas.setStrokeColor(colors.HexColor("#17291f"))
        canvas.setLineWidth(0.8)
        canvas.line(18 * mm, h - 18.5 * mm, w - 18 * mm, h - 18.5 * mm)
        canvas.setFont("KR", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.line(18 * mm, 12 * mm, w - 18 * mm, 12 * mm)
        canvas.drawString(18 * mm, 8.5 * mm, f"㈜지일  ·  작성 {now:%Y.%m.%d}  ·  공고문 원문과 대조 후 사용")
        canvas.drawRightString(w - 18 * mm, 8.5 * mm, f"{doc.page}")
        canvas.restoreState()

    def build(path: Path, story: list) -> None:
        SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=24 * mm, bottomMargin=16 * mm,
                          title="공고정보 요약", author="㈜지일").build(story, onFirstPage=frame, onLaterPages=frame)

    return SimpleNamespace(base=base, small=small, h1=h1, h2=h2, table=table, build=build, P=Paragraph, Spacer=Spacer)


# 요약 PDF의 원문 절은 '요약' — 줄 수·길이를 줄이고, 유의사항은 입찰에 직접 걸리는 줄만(2026-11-11 사용자: 원문을 빼곡히 옮기면 요약이 아님)
SHORT = {"참가 자격": (8, 90), "제출 서류": (10, 60), "유의사항·기타": (6, 70)}  # 절마다 (최대 줄 수, 줄 최대 글자)
_KEEP = re.compile(r"마감|기한|이내|제출|보증|지체|하자|실적|자격|공동|현장|설명회|무효|불가|금지|필수|반드시|한함|한정|"
                   r"단가|총액|낙찰|계약|기간|납품|배송|위약|감점|제한|등록|투찰")
_SKIP = re.compile(r"숙지|준용|문의|신고|책임은\s*(입찰|계약)|해석|협의하여|저작권|특허|보안|소유|연락처")
_ITEM = re.compile(r"^\s*(?:[○●◦ㅇo\-·※□■▶]|\d{1,2}[.)]|[가-하][.)]|\(\d{1,2}\)|[①-⑳])")


def _short(topic: str, lines: list[str]) -> tuple[list[str], int]:
    """(보여 줄 줄, 뺀 줄 수). 넘어간 줄은 앞 줄에 붙이고, 항목 줄만 남기고, 길면 자름."""
    rows: list[str] = []
    for line in lines:
        t = line.strip()
        if not t:
            continue
        if rows and not _ITEM.match(t):  # PDF·한글 줄바꿈으로 잘린 줄은 앞 항목에 붙임
            rows[-1] += " " + t
        else:
            rows.append(t)
    if topic == "유의사항·기타":
        rows = [r for r in rows if _KEEP.search(r) and not _SKIP.search(r)]
    limit, width = SHORT[topic]
    shown = [r if len(r) <= width else r[:width - 1] + "…" for r in rows[:limit]]
    return shown, len(rows) - len(shown)


def _original(k, sections: dict | None) -> list:
    """공고 문서의 참가 자격·제출 서류·유의사항 — 핵심 줄만 짧게(전문은 공고문)."""
    story = []
    for topic in ("참가 자격", "제출 서류", "유의사항·기타"):
        story.append(k.P(f"{topic} — 공고문 요약", k.h2))
        items = (sections or {}).get(topic) or []
        if topic == "유의사항·기타":  # 공고문 쪽만 — 과업지시서 일반 조항까지 넣으면 길어짐
            items = [it for it in items if "공고" in it[0]] or items[:1]
        if not items:
            story.append(k.P("공고 문서에서 이 절을 찾지 못함 — 공고문 확인", k.base))
        for name, head, lines in items:
            shown, cut = _short(topic, lines)
            story.append(k.P(f"<b>{esc(head)}</b> &nbsp;<font size=7 color='#777777'>({esc(name)})</font>", k.base))
            story += [k.P("&nbsp;&nbsp;" + esc(line), k.base) for line in shown]
            if cut:
                story.append(k.P(f"&nbsp;&nbsp;… 외 {cut}줄 — 전문은 공고문 참고", k.small))
            elif not shown:
                story.append(k.P("&nbsp;&nbsp;입찰에 직접 걸리는 내용 없음 — 전문은 공고문 참고", k.small))
    return story


def write_award_summary_pdf(path: Path, key: str, title: str, rows: list[tuple[str, str]], sections: dict | None,
                            files: list[str], now: datetime) -> bool:
    """낙찰 후 서류 공고문서/ 요약 — 1억 미만 요약과 같은 모양(기본 정보 + 공고문 원문 절). 글꼴이 없으면 False."""
    k = _kit(now)
    if k is None:
        return False
    story = [k.P(esc(title or ""), k.h1), k.P(esc(f"공고번호 {key}"), k.small), k.P("기본 정보", k.h2), k.table(rows)]
    story += _original(k, sections)
    story += [k.Spacer(1, 6), k.P("읽은 문서: " + esc(" / ".join(files) or "-"), k.small)]
    k.build(path, story)
    return True


def write_summary_pdf(candidate, amount: float | None, source: str, path: Path, key: str, need: list[tuple[str, str, str]],
                      ref: str, deadline: str | None, files: list[str], now: datetime, sections: dict | None = None,
                      forms: list[str] | None = None, ebid: bool = False) -> bool:
    """한글 글꼴이 없으면 False — 나머지 자료는 그대로 만든다."""
    from nego.text_requirements import summarize_flag
    from reportlab.lib import colors

    k = _kit(now)
    if k is None:
        return False
    base, small, h1, h2, table, Paragraph, Spacer = k.base, k.small, k.h1, k.h2, k.table, k.P, k.Spacer
    mark = {True: '<font color="#1b6e43"><b>[보유]</b></font>', False: '<font color="#c0392b"><b>[미보유]</b></font>',
            None: '<font color="#888888">[없어도 됨]</font>'}

    n = candidate.notice
    q = candidate.qualification
    story = [Paragraph(esc(n.title or ""), h1), Paragraph(esc(f"공고번호 {key}"), small), Paragraph("기본 정보", h2),
             table([("공고기관", n.notice_institution), ("수요기관", n.demand_institution or n.notice_institution),
                    ("업무", getattr(n, "work_type", None)), ("계약방법", getattr(n, "contract_method", None)),
                    ("낙찰방법", getattr(n, "award_method", None)), ("공동수급", candidate.joint.label)]),
             Paragraph("금액", h2), table(money_rows(candidate, amount, source) or [("금액", "찾지 못함 — 공고문 확인")]),
             Paragraph("일정", h2),
             table([(name, f"{when:%Y-%m-%d %H:%M}") for name, when in candidate.schedule.all_deadlines] or [("일정", "찾지 못함 — 공고문 확인")]),
             Paragraph("자격 (면허·업종·세부품명)", h2)]
    labels = license_labels(q)
    if labels:
        story.append(Paragraph("나라장터 면허제한: " + esc(" / ".join(labels)), small))
    if not q.checked:
        story.append(Paragraph("자격 판정 정보 없음 — 공고문 참가자격 절을 원문에서 확인", base))
    for part in getattr(candidate, "qual_parts", None) or []:
        reqs = list(part.get("missing") or []) + list(part.get("satisfied") or [])
        if not reqs:
            continue
        story.append(Paragraph(f"<b>{esc(part['name'])}</b> — {esc(part.get('status') or '')}", base))
        for head, lines in req_lines(reqs, mark):
            story.append(Paragraph(f"&nbsp;&nbsp;{head}", small))
            story += [Paragraph("&nbsp;&nbsp;&nbsp;&nbsp;" + line, base) for line in lines]
    region = candidate.region_check
    story += [Paragraph("지역 제한", h2),
              table([("판정", region.status + (f" (근거: {region.source})" if region.source else "")),
                     ("참가 가능 지역", ", ".join(region.required) or "제한 정보 없음"), ("지일 소재지", region.company or "-")]),
              Paragraph("확인 필요 요건", h2)]
    if candidate.text_flags:
        for flag in candidate.text_flags:
            story.append(Paragraph(f"<b>{esc(KIND_NAME.get(flag.get('kind'), str(flag.get('kind', ''))))}</b>", base))
            story += [Paragraph(f"&nbsp;&nbsp;{esc(str(k))}: {esc(str(v))}", base) for k, v in summarize_flag(flag) or []]
            story.append(Paragraph("&nbsp;&nbsp;원문: " + esc((flag.get("text") or "")[:300]), small))
    else:
        story.append(Paragraph("없음(실적·기술인력 요건 문장을 찾지 못함)", base))
    # 제출할 서식·입찰서 — 회사 서류만 보이면 서식을 빠뜨린 것처럼 읽힘(2026-11-12 사용자 제보)
    story.append(Paragraph("제출 서식 — 서식자동분리 폴더에 만들어 둠", h2))
    story += [Paragraph("&nbsp;&nbsp;" + esc(re.sub(r"^별도_", "", name)), base) for name in forms or []] or [
        Paragraph("첨부에 정해진 서식 없음", base)]
    if ebid:
        story.append(Paragraph("&nbsp;&nbsp;입찰서 — 나라장터 전자입찰로 제출(따로 만들 파일 없음)", base))
    story += [Paragraph("회사 서류 — 새로 준비할 것", h2),
              Paragraph(f"판단 기준일 {ref}" + (f" (제출 마감 {deadline})" if deadline and deadline >= ref else " (마감이 지났거나 못 찾음 — 오늘 기준)"), small)]
    if need:
        story += [table([(item, (f"{name} — " if name else "") + why) for item, name, why in need]),
                  Paragraph("유효기간이 지났거나 재발급이 필요한 서류는 회사준비서류 폴더에 넣지 않았습니다.", small)]
    else:
        story.append(Paragraph("없음 — 체크리스트의 증명서류는 회사 서류에서 모두 넣음", base))
    story += _original(k, sections)
    story += [Spacer(1, 6), Paragraph("읽은 문서: " + esc(" / ".join(files) or "-"), small)]
    k.build(path, story)
    return True
