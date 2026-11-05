"""공고정보_요약.pdf — 표본(1억미만_사업폴더_취합_20261028)과 같은 양식. 규칙으로만, AI 없음."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
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
    rows = [("추정가격", won(n.estimated_price)), ("배정예산", won(n.assigned_budget)),
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


def write_summary_pdf(candidate, amount: float | None, source: str, path: Path, key: str, need: list[tuple[str, str, str]],
                      ref: str, deadline: str | None, files: list[str], now: datetime) -> bool:
    """한글 글꼴이 없으면 False — 나머지 자료는 그대로 만든다."""
    from nego.text_requirements import summarize_flag
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
        return False
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
    mark = {True: '<font color="#1b6e43"><b>[보유]</b></font>', False: '<font color="#c0392b"><b>[미보유]</b></font>',
            None: '<font color="#888888">[없어도 됨]</font>'}

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
    story += [Paragraph("회사 서류 — 새로 준비할 것", h2),
              Paragraph(f"판단 기준일 {ref}" + (f" (제출 마감 {deadline})" if deadline and deadline >= ref else " (마감이 지났거나 못 찾음 — 오늘 기준)"), small)]
    if need:
        story += [table([(item, (f"{name} — " if name else "") + why) for item, name, why in need]),
                  Paragraph("유효기간이 지났거나 재발급이 필요한 서류는 회사준비서류 폴더에 넣지 않았습니다.", small)]
    else:
        story.append(Paragraph("없음 — 체크리스트의 증명서류는 회사 서류에서 모두 넣음", base))
    story += [Spacer(1, 6), Paragraph("읽은 문서: " + esc(" / ".join(files) or "-"), small)]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=24 * mm, bottomMargin=16 * mm,
                      title="공고정보 요약", author="㈜지일").build(story, onFirstPage=frame, onLaterPages=frame)
    return True
