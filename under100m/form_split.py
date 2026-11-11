"""공고 문서 HWPX의 서식을 서식마다 한글 파일로 잘라 낸다 — 표본(1억미만_사업폴더_취합_20261028)을 만든 규칙 그대로, AI 없음.

- 서식 시작 = 본문 맨 바깥 문단 중 글이 "【별지N】"·"[별지 제N호 서식]"·"【서식 제7-1호】" 등으로 시작하는 문단
- 서식 내용 = 그 문단부터 다음 서식 시작 앞까지(구역을 넘어가도 이어서). 원본 XML 문단을 글자 그대로 옮긴다.
- 첫 문단의 쪽 나누기를 끄고 쪽 설정을 그 문단에 붙인다(빈 첫 쪽이 생기지 않게). 안 쓰는 그림은 뺀다.
"""

from __future__ import annotations

import html
import logging
import re
import zipfile
from pathlib import Path

log = logging.getLogger("under100m")

TAG = re.compile(r'<hp:p(?=[\s>/])[^>]*?(/?)>|</hp:p>')
# 서식 머리 표기 — 【별지N】, [별지 제N호 서식], [서식N], <서식N>, <서식 제N호>, 【서식N】, [서식 4-1] … (발주처마다 다름)
MARK = re.compile(r'^\s*[【\[<〈《(〔［]\s*(별\s*지|서\s*식|양\s*식)\s*(?:제\s*)?(\d+(?:\s*-\s*\d+)?)\s*(?:호)?\s*(?:서\s*식)?\s*(?:,[^】\]>〉》)〕］]*)?[】\]>〉》)〕］]\s*(.*)$')
KINDS = r'별\s*지|서\s*식|양\s*식'
# 번호가 앞에 오는 모양 — <제1호 서식>, [제2호 별지] (북부경기)
MARK2 = re.compile(r'^\s*[【\[<〈《(〔［]\s*제\s*(\d+(?:\s*-\s*\d+)?)\s*호\s*(별\s*지|서\s*식|양\s*식)\s*[】\]>〉》)〕］]\s*(.*)$')


# 【붙임1】 서식 — 문서에 별지·서식·양식 표시가 하나도 없을 때만 씀(2026-10-29 국립중앙과학관 변경공고 "【붙임1】 … 서약서").
# 대괄호만 — "붙임 : 수의계약 배제사유 1부" 같은 첨부 목록 줄은 아님
MARK3 = re.compile(r'^\s*[【\[［]\s*(붙\s*임)\s*(\d+(?:\s*-\s*\d+)?)\s*[】\]］]\s*(.*)$')


# 번호 뒤 마침표와 이름이 괄호 안에 있는 모양 — [서식 1. 입찰 참가신청서] (2026-11-09 주진불빛공원 제안요청서: 앞 목록 줄만 서식으로 잘렸음)
MARK4 = re.compile(r'^\s*[【\[<〈《〔［]\s*(' + KINDS + r')\s*(?:제\s*)?(\d+(?:\s*-\s*\d+)?)\s*(?:호)?\s*[.．]\s*([^】\]>〉》〕］]{2,40})[】\]>〉》〕］]\s*(?:※.*)?$')  # 뒤 안내 "※ 참여자 모두 작성하여 제출"은 허용


def mark_match(t):
    """(종류, 번호, 뒤 글) 또는 None"""
    m = MARK4.match(t)
    if m: return m.group(1), m.group(2), m.group(3)
    m = MARK.match(t)
    if m: return m.group(1), m.group(2), m.group(3)
    m = MARK2.match(t)
    if m: return m.group(2), m.group(1), m.group(3)
    m = MARK3.match(t)
    if m: return m.group(1), m.group(2), m.group(3)
    return None


def fid(s):
    return re.sub(r'\s+', '', s)


def sort_key(n):
    return [int(x) for x in re.findall(r'\d+', n)]


def label_of(kind, n):
    return f'{kind}{n.zfill(2) if n.isdigit() else n}'


def top_paragraphs(xml):
    """(시작, 끝) — <hs:sec> 바로 아래 <hp:p> 문단의 글자 위치."""
    spans, depth, start = [], 0, None
    for m in TAG.finditer(xml):
        if m.group(0).startswith('</'):
            depth -= 1
            if depth == 0: spans.append((start, m.end()))
        elif m.group(1) == '/':
            if depth == 0: spans.append((m.start(), m.end()))
        else:
            if depth == 0: start = m.start()
            depth += 1
    return spans


def text_of(p):
    # XML 안의 &lt;서식 제1호&gt; 같은 대체 문자를 실제 글자로(강릉·경상남도 서식 머리가 "<서식 1>" 모양)
    return html.unescape(''.join(re.sub(r'<[^>]+>', '', t) for t in re.findall(r'<hp:t(?:\s[^>]*)?>(.*?)</hp:t>', p, flags=re.S)))


def balanced_run(first):
    """쪽 설정(secPr)이 든 글 조각 <hp:run>…</hp:run> 전체 — 안에 머리말·꼬리말(글 조각이 또 든 subList)이 있어도
    짝을 세어 바깥 닫는 태그까지(2026-10-29 국립중앙과학관 변경공고: 첫 안쪽 </hp:run>에서 잘려 파일이 손상됨)"""
    m = re.search(r'<hp:run(?:\s[^>]*)?>(?:(?!</hp:run>).)*?<hp:secPr', first, flags=re.S)
    if not m: return ''
    depth, pos = 0, m.start()
    for t in re.finditer(r'<hp:run(?:\s[^>]*)?(/?)>|</hp:run>', first[m.start():]):
        if t.group(0).startswith('</'): depth -= 1
        elif not t.group(1): depth += 1
        if depth == 0: return first[m.start():m.start() + t.end()]
    return ''


_TITLE_END = re.compile('|'.join(r'\s*'.join(word) for word in (
    '서약서', '각서', '신청서', '증명서', '확인서', '협정서', '선임계', '인감계', '위임장', '질의서', '동의서', '평가표', '제출서', '현황', '사항', '표지', '양식', '실적', '내용')) + r'(?:\s*\))?')


def tidy(t):
    """'입 찰 참 가 신 청 서 ※ 아래…' → '입찰참가신청서' — 한 글자씩 띄운 이름 붙이고 ※·괄호 안내 뒤는 자름"""
    t = re.split(r'※|\s{3,}|•', t)[0].strip()
    t = re.sub(r'^[^:：]{0,12}[:：]\s*', '', t, count=1)     # "접수방법 : 제공한 서면질의서" → "제공한 서면질의서"(앞 짧은 머리말만)
    t = re.sub(r'^(제공(된|한)|별첨|첨부)\s*', '', t)          # → "서면질의서"
    t = re.sub(r'[\s ]*\(\s*p[\s \dp,~\-]*\)', '', t)  # 강릉 "(p14)", "(p18 p19 p20)" 쪽 표시
    t = re.sub(r'[\s(\[<〈]*개정\s*[\d.\s]+[)\]>〉]?', '', t)  # "상세규격서 (개정 2020. 04. 06)"
    t = re.sub(r'^[<〈《「\s]+|[>〉》」\s]+$', '', t)         # "<제안업체 일반현황>"
    # 표 글이 이어 붙은 긴 이름("서 약 서상호 또는법인 명칭…") — 서식 이름 끝말까지만(2026-11-05 대구·산불 공고)
    if len(t) > 20:
        m = _TITLE_END.search(t)
        if m:
            end = m.end()
            while (more := _TITLE_END.match(t, end)):  # "실적증명서"를 "실적"에서 끊지 않게(2026-11-09 대구 용역이행 실적증명서)
                end = more.end()
            t = t[:end]
    t = re.sub(r'(?:(?<=^)|(?<=\s))((?:[가-힣]\s+)+[가-힣])(?=\s|$)', lambda m: re.sub(r'\s', '', m.group(1)), t)
    if t.count('(') > t.count(')'):
        t += ')'  # "제안서(표지"
    return t.strip(' _')[:45]


def split_forms(SRC: Path, OUT: Path, all_forms: bool = False) -> tuple[list[str], list[str]]:
    """(만든 파일 이름들, 제출서류 체크리스트 줄)."""
    z = zipfile.ZipFile(SRC)
    sections = sorted((n for n in z.namelist() if re.match(r'Contents/section\d+\.xml$', n)), key=lambda n: int(re.findall(r'\d+', n)[0]))
    docs = {}
    for s in sections:
        xml = z.read(s).decode('utf-8')
        spans = top_paragraphs(xml)
        first = xml[spans[0][0]:spans[0][1]]
        sec_run = balanced_run(first)
        # 쪽 설정(secPr)이 든 첫 글 조각에 원본 첫 쪽 그림(발주처 로고)이 같이 묶여 있으면 서식마다 로고가 복사됨 — 그림은 뺌
        # (2026-10-28 유아숲 "함께 여는 미래" 로고가 서식 24개 모두에 붙었음)
        sec_run_xml = re.sub(r'<hp:pic\b.*?</hp:pic>', '', sec_run, flags=re.S) if sec_run else ''
        docs[s] = {'xml': xml, 'spans': spans, 'head': xml[:spans[0][0]], 'tail': xml[spans[-1][1]:], 'sec_run': sec_run_xml}

    # 서식 시작 위치 찾기
    raw_starts, inline = [], {}
    for s in sections:
        d = docs[s]
        for i, (a, b) in enumerate(d['spans']):
            t = text_of(d['xml'][a:b])
            m = mark_match(t)
            if m and len(t.strip()) <= 60:  # 긴 문장 속 "[서식 1] 참조"는 서식 시작이 아님
                kind, num, rest = m
                raw_starts.append((s, i, fid(num), re.sub(r'\s+', '', kind)))
                if rest.strip(): inline.setdefault(fid(num), rest.strip())
    if any(k != '붙임' for *_, k in raw_starts):
        raw_starts = [x for x in raw_starts if x[3] != '붙임']
    order =[(s, i) for s in sections for i in range(len(docs[s]['spans']))]
    pos = {k: n for n, k in enumerate(order)}
    # 서식 목록(제목 줄 3개 이상이 연달아) 줄은, 같은 번호의 실제 서식 머리가 따로 있으면 버림 — 목록 끝 줄이 뒤 내용(표지 등)을
    # 끌고 가 다른 서식에 붙었음(2026-11-09 주진불빛공원: <서식 25> 목록 줄 + 서식1 표지)
    at = sorted(pos[(s, i)] for s, i, *_ in raw_starts)
    run, listy = [], set()
    for p in at + [None]:
        if run and (p is None or p != run[-1] + 1):
            if len(run) >= 3:
                listy.update(run)
            run = []
        if p is not None:
            run.append(p)
    real = {x[2] for x in raw_starts if pos[(x[0], x[1])] not in listy}
    toc = [x for x in raw_starts if pos[(x[0], x[1])] in listy and x[2] in real]
    raw_starts = [x for x in raw_starts if x not in toc]
    # 같은 번호가 여러 번이면(목차 줄 "[서식1] 입찰참가신청서" + 실제 서식) 다음 머리까지 가장 긴 것을 실제 서식으로
    seg = {}
    for k, (s, i, n, kind) in enumerate(raw_starts):
        end = pos[(raw_starts[k + 1][0], raw_starts[k + 1][1])] if k + 1 < len(raw_starts) else len(order)
        if n not in seg or end - pos[(s, i)] > seg[n][1]:
            seg[n] = ((s, i, n, kind), end - pos[(s, i)])
    keep = {v[0] for v in seg.values()}
    starts = [x for x in raw_starts if x in keep]
    # 서식 이름: 목차 문단("별지1. 입찰참가신청서") → 머리 뒤 글("[서식1] 입찰참가신청서") → 머리 다음 문단 첫 글
    names = {}
    for s in sections:
        d = docs[s]
        for a, b in d['spans']:
            m = re.match(r'^\s*(?:' + KINDS + r')\s*(\d+(?:\s*-\s*\d+)?)\s*[.．]\s*(.+)$', text_of(d['xml'][a:b]).strip())
            if m: names.setdefault(fid(m.group(1)), m.group(2).strip())
    def expand(s):
        """'5~10, 15' → 5…10, 15 / '4-1' → 4-1(하위 번호) / '서식 1, 2' → 1, 2"""
        out = []
        for a, b in re.findall(r'(\d+(?:-\d+)?)\s*(?:[~∼]\s*(\d+))?', re.sub(r'\s*-\s*', '-', s)):
            out += [str(x) for x in range(int(a), int(b) + 1)] if b and a.isdigit() else [a]
        return out


    # 본문(첫 서식 앞)의 "[서식1] 입찰참가신청서" 언급 — 서식 이름과, 제출서류 표가 없을 때 '제출할 서식' 목록으로
    first_form = pos[(starts[0][0], starts[0][1])] if starts else len(order)
    body = ' '.join(text_of(docs[s]['xml'][docs[s]['spans'][i][0]:docs[s]['spans'][i][1]]) for s, i in order[:first_form])
    REF = re.compile(r'[\[【<〈〔［]\s*(?:' + KINDS + r')\s*(?:제\s*)?([\d\s호~∼,\-]+?)\s*(?:서\s*식)?\s*[\]】>〉〕］]\s*([가-힣][가-힣\d\s·‧ㆍ()]{1,30})?')  # ‧: 수집‧이용‧제공(주진불빛공원)
    body_refs, ref_cands, guessed = {}, {}, set()
    for m in REF.finditer(body):
        nums = expand(m.group(1).replace('호', ''))  # "6호 ~ 13호" → 6…13
        # 이름은 앞 글("- 청렴계약이행서약서 1부 <서식 2호>")이나 뒤 글("[서식1] 입찰참가신청서")
        before = re.split(r'[-·•▪□○)\]]\s*', body[max(0, m.start() - 40):m.start()])[-1]
        before = re.sub(r'\d+\s*부\s*$', '', before).strip()
        before = re.split(r'\d+\s*\.(?!\d)|[,*·]', before)[-1].strip()  # "불가 2. 입찰참가신청서" → "입찰참가신청서"
        for n in nums:
            ref_cands.setdefault(n, {'after': [], 'before': []})
            if len(nums) == 1:
                ref_cands[n]['after'].append(tidy(m.group(2) or ''))
                if before: ref_cands[n]['before'].append(tidy(before))
    FORM_END = re.compile(r'(서|장|표|계|지|원|계획|체계|현황|사항|실적|내용|각서|확인서|증명서|신청서|협정서|선임계|표지)(\s*\([^)]*\))?$')
    JOSA = re.compile(r'^(에|을|를|의|은|는|으로|로)(\s|$)')
    for n, c in ref_cands.items():
        ok = lambda s: len(s) >= 2 and FORM_END.search(s) and not JOSA.match(s) and not re.search(r'[.,「」]|\d\.|서식|제\s*\d+\s*호|번호$', s)
        after = [s for s in c['after'] if ok(s)]
        before = [s for s in c['before'] if ok(s) and len(s) <= 16]
        # "[서식1] 입찰참가신청서"처럼 뒤에 적힌 이름이 먼저, 없을 때만 "- 청렴계약이행서약서 1부 <서식 2호>"처럼 앞 글
        body_refs[n] = max(after, key=len) if after else (min(before, key=len) if before else '')
        if not after and before:
            guessed.add(n)  # 앞 글에서 짐작한 이름 — 머리 줄에 이름이 있으면 그쪽을 씀(주진불빛공원 서식16이 앞 줄 '제안서'를 가져감)
    # 빠진 번호 — 발주처가 서식 표시를 빼먹은 경우(2026-10-28 경상남도: "<서식 1>" 신청서 표 바로 뒤에 "위 임 장" 제목과 표, 다음이 "<서식 3>").
    # 앞뒤 번호 사이에 빠진 번호가 하나면, 앞 서식 안에서 본문이 말한 그 서식 이름이나 서식 제목처럼 생긴 짧은 줄 + 바로 뒤 표를 찾아 거기서 나눔
    TITLE_LINE = re.compile(r'^((?:[가-힣]\s*){2,12})$')
    FORM_NOUN = re.compile(r'(서|장|계|표|각서|현황|확인서|증명서|신청서)$')
    extra = []
    for k in range(len(starts) - 1):
        (s1, i1, n1, kind1), (s2, i2, n2, _) = starts[k], starts[k + 1]
        if not (n1.isdigit() and n2.isdigit() and int(n2) - int(n1) == 2): continue
        miss = str(int(n1) + 1)
        if miss in {x[2] for x in starts}: continue
        want = re.sub(r'\s+', '', body_refs.get(miss, ''))
        a, b = pos[(s1, i1)] + 1, pos[(s2, i2)]
        for j in range(a + 1, b - 1):
            sj, ij = order[j]
            t = re.sub(r'\s+', ' ', text_of(docs[sj]['xml'][docs[sj]['spans'][ij][0]:docs[sj]['spans'][ij][1]])).strip()
            nxt_sj, nxt_ij = order[j + 1]
            nxt_has_tbl = '<hp:tbl' in docs[nxt_sj]['xml'][docs[nxt_sj]['spans'][nxt_ij][0]:docs[nxt_sj]['spans'][nxt_ij][1]]
            sq = t.replace(' ', '')
            if TITLE_LINE.match(t) and FORM_NOUN.search(sq) and nxt_has_tbl and (not want or want in sq or sq in want):
                extra.append((sj, ij, miss, kind1))
                names.setdefault(miss, sq)
                log.info('빠진 서식 표시 보충: %s번 = %s', miss, sq)
                break
    if extra:
        starts = sorted(starts + extra, key=lambda x: pos[(x[0], x[1])])
    # 앞쪽 서식 목록 줄을 버리고 나면, 목록 끝과 첫 서식 머리 사이의 내용(표지 등)은 첫 서식 것 —
    # 주진불빛공원 "<서식 1> 입찰 참가 신청서(표지 포함)": 표지가 "[서식 1. …]" 머리 앞에 있음
    begin = {}
    if starts:
        first = pos[(starts[0][0], starts[0][1])]
        dropped = [pos[(x[0], x[1])] for x in raw_starts + toc if x not in keep and pos[(x[0], x[1])] < first]
        if dropped:
            b = max(dropped) + 1
            while b < first:
                sb, ib = order[b]
                q = docs[sb]['xml'][docs[sb]['spans'][ib][0]:docs[sb]['spans'][ib][1]]
                t = text_of(q).strip()
                if '<hp:tbl' not in q and (not t or re.match(r'^[【\[<〈《〔［]', t)):
                    b += 1  # 빈 줄·목록 줄(<별표1> …)은 건너뜀
                else:
                    break
            if b < first:
                begin[starts[0][2]] = b
    for s, i, n, kind in starts:
        if n in names: continue
        d = docs[s]
        title = ''
        for j in range(i + 1, min(i + 10, len(d["spans"]))):
            t = re.sub(r'\s+', ' ', text_of(d['xml'][d['spans'][j][0]:d['spans'][j][1]])).strip()
            # 사업명 『…』 줄, 서명란 "(인) …귀하" 줄, 다음 서식 머리 줄, "접수번호" 같은 칸 이름은 서식 이름 아님
            # (2026-10-29 북부경기: 서식03 표 글에 "귀하"가 있어 건너뛰고 "<제4호 서식>"을, 서식06은 "접수번호"를 이름으로 잡았음)
            if (len(t) >= 2 and not re.match(r'^[\d\s]+$|^■|^[『「]|^\(인\)', t) and not (len(t) <= 40 and '귀하' in t)
                    and not mark_match(t) and not re.search(r'번\s*호$', t)):
                title = tidy(t); break
        ref = body_refs.get(n) or ''
        inl = tidy(inline.get(n, ''))
        # 머리 뒤 글이 근거 규정의 서식 번호("정보통신보안업무지침【서식 제5호】")나 안내("정성제안서에 포함")면 이름 아님
        if re.search(r'[【\[<〈]\s*(서\s*식|별\s*지|양\s*식)|제\s*\d+\s*호|(포함|참조|작성|제출|사용)$', inl): inl = ''
        # 서식에 직접 적힌 이름이 먼저 — 본문 언급은 다른 서식 이름이 붙기도 함(2026-11-05 대구: 입찰참가신청서 → "서류․입찰참가접수확인서")
        if len(inl) >= 2: names[n] = inl
        elif title and FORM_END.search(title.replace(' ', '')): names[n] = title
        elif len(ref) >= 2: names[n] = ref
        elif title: names[n] = title

    # 제출서류 목록 표(머리에 '제출서류'·'서식') — 줄마다 서류 이름·비고(필수/대리인 접수시 …)·별지 번호. 표가 있으면 표에 나온 별지만 만든다
    def cell_text(x):
        return html.unescape(' '.join(re.sub(r'<[^>]+>', '', t).strip() for t in re.findall(r'<hp:t(?:\s[^>]*)?>(.*?)</hp:t>', x, flags=re.S)).strip())


    def form_nums(cell):
        """서식 칸 글에서 번호들 — '별지2,4, 13' / '서식 1' / '[서식 3-1]'"""
        m = re.search(r'(?:' + KINDS + r')\s*(?:제\s*)?(?=\d)', cell)  # 표시 바로 뒤가 번호일 때만 — "서식(표지) … A4(210㎜)"의 숫자를 번호로 읽었음(2026-11-05)
        return expand(cell[m.end():]) if m else []


    submit_rows, wanted = [], {}
    for s in sections:
        for tbl in re.findall(r'<hp:tbl\b.*?</hp:tbl>', docs[s]['xml'], flags=re.S):
            # 칸마다 (열, 줄, 열 병합, 줄 병합, 글) — 한글 표의 cellAddr·cellSpan을 그대로 읽는다(병합 칸을 칸 수로 짐작하지 않음)
            cells = []
            for c in re.findall(r'<hp:tc\b.*?</hp:tc>', tbl, flags=re.S):
                a = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', c)
                sp = re.search(r'<hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"', c)
                if a:
                    cells.append((int(a.group(1)), int(a.group(2)), int(sp.group(1)) if sp else 1, int(sp.group(2)) if sp else 1, cell_text(c)))
            head = {col: t for col, row, cs, rs, t in cells if row == 0}
            hj = ' '.join(head.values()).replace(' ', '')
            if not (re.search(r'서식|양식', hj) and re.search(r'제출|서류', hj)):
                continue

            def col_of(*words):
                return next((col for w in words for col, t in head.items() if w in t.replace(' ', '')), None)

            def at(row, col):
                return next((x for x in cells if col is not None and x[0] <= col < x[0] + x[2] and x[1] <= row < x[1] + x[3]), None)

            c_item, c_note, c_form = col_of('제출서류', '구비서류', '서류명', '서류', '제출'), col_of('비고', '구분'), col_of('서식', '양식')
            if c_item is None or c_form is None or c_item == c_form:
                continue
            for row in range(1, max(x[1] + x[3] for x in cells)):
                it, nt, fm = at(row, c_item), at(row, c_note), at(row, c_form)
                if not it or it[1] != row:
                    continue
                nums = form_nums(fm[4]) if fm else []
                shared = fm is not None and fm[3] > 1  # 서식 칸이 여러 줄에 걸쳐 합쳐짐 — 그 줄들 서류의 서식이 모여 있음
                submit_rows.append((it[4], nt[4] if nt else '', nums, shared and fm[1] != row))
                for n in nums:
                    wanted.setdefault(n, nt[4] if nt else '')
    use_table = bool(wanted)
    # 제출서류가 표 칸이 아니라 글머리 목록("•[서식1] 입찰참가신청서 •[서식2] …")인 공고 — 본문에서 언급된 서식을 '제출할 서식'으로
    ref_mode = False
    if not use_table and body_refs:
        for n, t in body_refs.items():
            wanted[n] = ''
            submit_rows.append((f'[{t or "서식"}]', '', [n], False))
        use_table = ref_mode = True
    # 별지 번호가 안 적힌 줄이라도 서류 이름이 서식 이름에 들어 있으면 그 서식도(2026-10-28 사용자 요청 — "제안서" 줄 → 별지23 제안서 표지)
    squash = lambda s: re.sub(r'[\s①-⑳]|사본|원본', '', s or '')
    for n_row, (item, note, nums, inherit) in enumerate(submit_rows):
        if nums or inherit: continue
        key = squash(item)
        for num, title in names.items():
            if num not in wanted and len(key) >= 3 and key in squash(title):
                wanted[num] = note
                submit_rows[n_row] = (item, note, [num], inherit)

    OUT.mkdir(parents=True, exist_ok=True)
    hpf = z.read('Contents/content.hpf').decode('utf-8')
    header = z.read('Contents/header.xml').decode('utf-8')
    made = []
    skipped = []
    # 표지 서식(제안서 표지·평가서 표지)은 제출서류 목록에 없어도 늘 만듦(2026-10-28 — 에코팜랜드 별지23, 경상남도 서식4)
    if use_table:
        for s, i, n, kind in starts:
            if n not in wanted and '표지' in names.get(n, ''):
                wanted[n] = ''
                submit_rows.append((f'[{names[n]}]', '', [n], False))
    # 이 공고는 문서 안 서식을 모두 원할 때(2026-10-29 도로교통공단 서식 6~10)
    if use_table and all_forms:
        for s, i, n, kind in starts:
            if n not in wanted:
                wanted[n] = ''
                submit_rows.append((f'[{names.get(n, "")}] (제출서류 표에는 없음)', '', [n], False))
    kinds ={n: kind for s, i, n, kind in starts}
    logos_removed = []
    for k, (s, i, num, kind) in enumerate(starts):
        if use_table and num not in wanted:
            skipped.append(num); continue
        end = pos[(starts[k + 1][0], starts[k + 1][1])] if k + 1 < len(starts) else len(order)
        picked = order[begin.get(num, pos[(s, i)]):end]
        by_sec = {}
        for sec, idx in picked: by_sec.setdefault(sec, []).append(idx)
        files = {}
        for sec, idxs in by_sec.items():
            d = docs[sec]
            paras = [d['xml'][d['spans'][j][0]:d['spans'][j][1]] for j in idxs]
            # 첫 문단: 쪽 나누기 끄고, 구역 첫 문단이 아니면 쪽 설정(secPr) 붙이기
            paras[0] = re.sub(r'pageBreak="1"', 'pageBreak="0"', paras[0], count=1)
            # 서식 머리 줄([서식2] 줄)에 붙은 발주처 로고 그림은 뺌 — 자리를 많이 차지(2026-10-28 유아숲 "함께 여는 미래" 로고)
            if sec == picked[0][0] and '<hp:pic' in paras[0] and '<hp:tbl' not in paras[0]:  # 표지 표 안 그림은 둠
                n_pic = len(re.findall(r'<hp:pic\b', paras[0]))
                paras[0] = re.sub(r'<hp:pic\b.*?</hp:pic>', '', paras[0], flags=re.S)
                paras[0] = re.sub(r'<hp:linesegarray>.*?</hp:linesegarray>', '', paras[0], flags=re.S)
                logos_removed.append((num, n_pic))
            if idxs[0] != 0 and d['sec_run']:
                if paras[0].endswith('/>'):
                    paras[0] = paras[0][:-2] + '>' + d['sec_run'] + '</hp:p>'
                else:
                    paras[0] = re.sub(r'^(<hp:p(?=[\s>])[^>]*>)', lambda m: m.group(1) + d['sec_run'], paras[0], count=1)
            files[sec] = d['head'] + ''.join(paras) + d['tail']
        kept = list(by_sec)
        new_hpf = hpf
        for sec in sections:
            if sec not in kept:
                sid = Path(sec).stem
                new_hpf = re.sub(rf'<opf:item id="{sid}"[^>]*/>', '', new_hpf)
                new_hpf = re.sub(rf'<opf:itemref idref="{sid}"[^>]*/>', '', new_hpf)
        # 남는 구역을 section0, section1 … 순서로 다시 이름 붙임
        rename = {sec: f'Contents/section{n}.xml' for n, sec in enumerate(kept)}
        for old, new in rename.items():
            if old != new:
                so, sn = Path(old).stem, Path(new).stem
                new_hpf = new_hpf.replace(f'id="{so}" href="{old}"', f'id="__{sn}" href="{new}"').replace(f'idref="{so}"', f'idref="__{sn}"')
        new_hpf = new_hpf.replace('id="__', 'id="').replace('idref="__', 'idref="')
        new_header = re.sub(r'secCnt="\d+"', f'secCnt="{len(kept)}"', header)
        # 이 서식에서 안 쓰는 그림(BinData)은 뺀다 — 원본 그림 10개를 다 들고 가면 파일마다 2.8MB
        used = set(re.findall(r'binaryItemIDRef="([^"]+)"', ''.join(files.values()) + new_header))
        drop = set()
        for m in re.finditer(r'<opf:item id="([^"]+)" href="(BinData/[^"]+)"[^>]*/>', new_hpf):
            if m.group(1) not in used: drop.add(m.group(2))
        for href in drop:
            new_hpf = re.sub(rf'<opf:item id="[^"]+" href="{re.escape(href)}"[^>]*/>', '', new_hpf)
        title = names.get(num, '')
        note = wanted.get(num, '')
        # 비고가 조건(대리인 접수시 등)일 때만 붙임 — "필수", 쪽 번호("p14") 등은 안 붙임
        cond = f' ({note})' if note and note != '필수' and not re.fullmatch(r'[p\s \d,~\-]+', note) else ''
        fname = re.sub(r'[\\/:*?"<>|]', '_', f'{label_of(kind, num)}_{title}{cond}'.strip('_'))[:80].strip() + '.hwpx'
        target = OUT / fname
        with zipfile.ZipFile(target, 'w') as w:
            for info in z.infolist():
                n = info.filename
                if n in drop or re.match(r'Contents/section\d+\.xml$', n) or n in ('Contents/content.hpf', 'Contents/header.xml', 'Preview/PrvText.txt', 'Preview/PrvImage.png'):
                    continue
                # 원본 ZipInfo를 넘기면 쓰는 쪽이 그 객체의 위치값을 바꿔 원본 읽기가 깨진다 — 새로 만든다
                w.writestr(zipfile.ZipInfo(n, info.date_time), z.read(n),
                           compress_type=zipfile.ZIP_STORED if n == 'mimetype' else zipfile.ZIP_DEFLATED)
            w.writestr('Contents/header.xml', new_header, compress_type=zipfile.ZIP_DEFLATED)
            w.writestr('Contents/content.hpf', new_hpf, compress_type=zipfile.ZIP_DEFLATED)
            for old, new in rename.items():
                w.writestr(new, files[old], compress_type=zipfile.ZIP_DEFLATED)
        made.append((num, title, len(picked), ', '.join(Path(x).stem for x in kept), target.name))
    if use_table:
        no_form = sorted(set(wanted) - {s[2] for s in starts}, key=sort_key)
        lines = ['제출서류 체크리스트 — 제안요청서 "제출서류 목록" 표 기준 (자동 생성)', '']
        for item, note, nums, inherit in submit_rows:
            have = [label_of(kinds.get(n, '서식'), n) for n in nums if n in {m[0] for m in made}]
            where = ('위 줄과 같은 서식 묶음(' + ', '.join(have) + ') 중 해당 별지' if inherit and have else
                     ', '.join(have) + ' 파일' if have else '서식 없음 — 직접 준비')
            lines.append(f'[ ] {item}  ({note or "-"})  →  {where}')
        if no_form: lines += ['', f'표에는 있는데 문서 안에서 서식을 못 찾은 번호: {no_form}']
        if skipped: lines += ['', f'제안요청서엔 있지만 제출서류 표에 없어 안 만든 서식: {skipped} (' + ', '.join(names.get(n, '') for n in skipped) + ')']
    else:
        lines = ['제출서류 체크리스트 — 제안요청서에 서식 번호가 적힌 제출서류 표가 없어, 문서 안 서식을 모두 만들었습니다 (자동 생성)', '']
        lines += [f'[ ] {m[4]}' for m in made]
        lines += ['', '공고문의 제출서류 목록과 대조해 필요 없는 서식은 빼 주세요.']
    return [m[4] for m in made], lines


_SEC_NAME = re.compile(r'Contents/section(\d+)\.xml$')


def merge_forms(files: list[Path], target: Path) -> int:
    """같은 공고 문서에서 잘라 채운 서식들을 한 파일로 합친다 — 서식마다 구역 하나(쪽 설정·가로세로 그대로, 새 쪽에서 시작).
    서식 채우기가 파일마다 더한 문단 모양(paraPr)은 번호가 겹칠 수 있어 새 번호로 바꾼다. 돌려주는 값: 합친 구역 수.
    (2026-11-09 사용자 요청: 서식을 하나씩 나누지 말고 하나로)"""
    zs = [zipfile.ZipFile(f) for f in files]
    try:
        return _merge(zs, target)
    finally:
        for z in zs:
            z.close()


def _merge(zs: list[zipfile.ZipFile], target: Path) -> int:
    heads = [z.read('Contents/header.xml').decode('utf-8') for z in zs]
    para_ids = [set(re.findall(r'<hh:paraPr id="(\d+)"', h)) for h in heads]
    common = set.intersection(*para_ids) if para_ids else set()
    head = heads[0]
    next_id = max([int(i) for i in para_ids[0]] or [-1]) + 1
    sections, bins, bin_items = [], {}, {}
    for k, z in enumerate(zs):
        remap = {}
        if k:
            for pid in sorted(para_ids[k] - common, key=int):  # 이 서식 채우기에서 더한 문단 모양
                src = re.search(rf'<hh:paraPr id="{pid}"(?:[^>]*/>|.*?</hh:paraPr>)', heads[k], flags=re.S).group(0)
                remap[pid] = str(next_id)
                head = head.replace('</hh:paraProperties>', re.sub(r'<hh:paraPr id="\d+"', f'<hh:paraPr id="{next_id}"', src, count=1) + '</hh:paraProperties>', 1)
                next_id += 1
        names = sorted((n for n in z.namelist() if _SEC_NAME.match(n)), key=lambda n: int(_SEC_NAME.match(n).group(1)))
        for n in names:
            x = z.read(n).decode('utf-8')
            if remap:
                x = re.sub(r'paraPrIDRef="(\d+)"', lambda m: f'paraPrIDRef="{remap.get(m.group(1), m.group(1))}"', x)
            sections.append(x)
        hpf = z.read('Contents/content.hpf').decode('utf-8')
        for m in re.finditer(r'<opf:item id="([^"]+)" href="(BinData/[^"]+)"[^>]*/>', hpf):
            if m.group(2) in z.namelist() and m.group(2) not in bins:
                bins[m.group(2)] = z.read(m.group(2))
                bin_items.setdefault(m.group(1), m.group(0))
    head = re.sub(r'(<hh:paraProperties itemCnt=")\d+', lambda m: m.group(1) + str(len(re.findall(r'<hh:paraPr id=', head))), head, count=1)
    head = re.sub(r'secCnt="\d+"', f'secCnt="{len(sections)}"', head)
    hpf = zs[0].read('Contents/content.hpf').decode('utf-8')
    item = re.search(r'<opf:item id="section\d+"[^>]*/>', hpf).group(0)
    ref = re.search(r'<opf:itemref idref="section\d+"[^>]*/>', hpf).group(0)
    hpf = re.sub(r'<opf:item id="section\d+"[^>]*/>|<opf:itemref idref="section\d+"[^>]*/>|<opf:item id="[^"]+" href="BinData/[^"]+"[^>]*/>', '', hpf)
    new_items = ''.join(re.sub(r'section\d+', f'section{i}', item) for i in range(len(sections))) + ''.join(bin_items.values())
    hpf = hpf.replace('</opf:manifest>', new_items + '</opf:manifest>', 1)
    hpf = hpf.replace('</opf:spine>', ''.join(re.sub(r'section\d+', f'section{i}', ref) for i in range(len(sections))) + '</opf:spine>', 1)
    base = zs[0]
    with zipfile.ZipFile(target, 'w') as w:
        for info in base.infolist():
            n = info.filename
            if _SEC_NAME.match(n) or n.startswith('BinData/') or n in ('Contents/content.hpf', 'Contents/header.xml'):
                continue
            w.writestr(zipfile.ZipInfo(n, info.date_time), base.read(n),
                       compress_type=zipfile.ZIP_STORED if n == 'mimetype' else zipfile.ZIP_DEFLATED)
        w.writestr('Contents/header.xml', head, compress_type=zipfile.ZIP_DEFLATED)
        w.writestr('Contents/content.hpf', hpf, compress_type=zipfile.ZIP_DEFLATED)
        for i, x in enumerate(sections):
            w.writestr(f'Contents/section{i}.xml', x, compress_type=zipfile.ZIP_DEFLATED)
        for n, data in bins.items():
            w.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)
    return len(sections)
