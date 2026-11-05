"""잘라 낸 서식(HWPX)의 회사 정보 칸을 채운다 — 표본을 만든 규칙 그대로, AI 없음.

① 표: 이름 칸(업체명·상호·대표자·주소·사업자번호 …) 오른쪽 칸이 비어 있으면 값을 넣음
② 줄: "업 체 명 :", "대 표 자 :   (인)"처럼 이름 + 쌍점 뒤가 비어 있으면 값을 넣음
③ 입찰일자 칸만 입찰 마감일 — 서류 날짜는 담당자가 정함(2026-10-28 사용자 결정)

회사 값은 공개 저장소에 두지 않고 NAS 공유 폴더의 회사 프로필에서 읽는다(`load_company`):
  {"회사": {name·ceo·addr·bizno·corpno·tel·fax·rrn}, "칸": {칸 이름: {"빈칸", "지일 값"}}, "현재대표자", "대표변경일"}
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from pathlib import Path

# 담당자가 직접 쓰는 칸 — 지일 작성판에 값이 있어도 안 채움(2026-10-28 사용자 결정: 때마다 바뀌는 값은 자동으로 넣지 않음)
MANUAL = {"인력구성현황", "면허/허가/등록증보유현황", "자본금(입찰공고일기준)", "자본금"}
JIIL: dict[str, str] = {}
PROFILE: dict[str, dict] = {}
BID_DATE: str | None = None
TITLE: str | None = None
NOTICE_NO: str | None = None


def load_company(path: Path) -> dict:
    """없으면 FileNotFoundError — 회사 정보 없이 빈 서식을 담당자에게 주지 않게 실행을 멈추고 알린다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not data.get("회사", {}).get("name"):
        raise ValueError(f"{path}에 회사 정보가 없습니다")
    return data


def _configure(company: dict, bid_date: str | None, title: str | None, notice_no: str | None) -> None:
    global BID_DATE, TITLE, NOTICE_NO
    BID_DATE, TITLE, NOTICE_NO = bid_date or None, title or None, notice_no or None
    JIIL.clear()
    JIIL.update(company["회사"])
    if TITLE:
        JIIL["title"] = TITLE
    # "법인등록번호(사업자등록번호)" = 법인은 법인번호 — 지일은 법인이라 앞 번호 하나만(2026-10-28 사용자: 말도 안 되는 것 넣지 말 것)
    JIIL["corp_biz"] = JIIL.get("corpno", "")
    JIIL["biz_corp"] = JIIL.get("bizno", "")
    # 서약서 "서약자 (인)" — 지일 작성판 "서약자 ㈜지일 대표 ○○○ (인)" 모양
    JIIL["sig"] = f"{JIIL['name']} 대표 {JIIL.get('ceo', '')}"
    PROFILE.clear()
    for key, value in company.get("칸", {}).items():
        if not (key in MANUAL or re.fullmatch(r"\(?\d{4}\)?", key) or re.search(r"매출|자본금|인력|면허|직원수|종업원", key)):
            PROFILE[key] = value


FIELDS = [  # (칸 이름 — 공백 무시, 값 키)
    # (2026-10-29 강릉 실적증명서 "업 체 명 (기관명)"·"영업장소재지")
    (r'업체명(\((상호|기관명|법인명)\))?|상호(또는법인명칭|\((법인명칭|기관명)\))?|회사명|사업체명|제안업체명|업체명칭|법인명칭|제안자|소속|신청인|기관명', 'name'),  # 기관명(2026-10-29 도로교통공단 서약서 — 제출자 칸)
    (r'대표자(성명|명)?|대표(자)?\(성명\)|제안업체대표|대표이사', 'ceo'),  # (2026-10-29 북부경기 회사 일반현황 "대표자명")
    (r'주소(\(소재지\))?|소재지(\(주소\))?|영업(장|소)?소재지|사업장소재지|본사소재지|본점소재지', 'addr'),
    (r'사업자(등록)?번호', 'bizno'),
    # 법인은 법인번호, 개인은 주민번호를 적는 칸(2026-10-29 유아숲 신용평가등급확인서 "법인등기부등본(주민등록번호)")
    (r'법인(등록)?번호|법인(등록)?번호\(주민등록번호\)|법인등기부등본\(주민등록번호\)', 'corpno'),
    (r'전화번호|대표전화|TEL', 'tel'),
    (r'서약자', 'sig'),
    (r'FAX(번호)?|팩스(번호)?', 'fax'),
    (r'주민등록번호', 'rrn'),
    # 두 번호가 한 칸 — 앞 번호만(2026-10-28 제주별빛누리 보안서약서 "법인등록번호(사업자등록번호)")
    (r'법인(등록)?번호\(사업자(등록)?번호\)', 'corp_biz'), (r'사업자(등록)?번호\(법인(등록)?번호\)', 'biz_corp'),
    (r'입찰건명|건명|사업명|용역명|과업명|공사명|계약건명|입찰명', 'title'),
]


def key_of(label):
    s = re.sub(r'\s+', '', label).rstrip(':：')
    s = re.sub(r'^\d{1,2}\.(?!\d)', '', s)  # "1. 회 사 명"(2026-10-29 도로교통공단 일반현황)
    k = next((k for rx, k in FIELDS if re.fullmatch(rx, s)), None)
    return k if JIIL.get(k) else None


def text(x):
    return html.unescape(''.join(re.sub(r'<[^>]+>', '', s) for s in re.findall(r'<hp:t(?:\s[^>]*)?>(.*?)</hp:t>', x, flags=re.S))).strip()


MARK = '⁣'  # 글을 넣은 자리 표시(보이지 않는 글자) — 끝에 그 문단의 줄 배치 정보를 지우고 없앤다


def drop_layout(xml):
    """글을 넣은 문단의 <hp:linesegarray>(한글이 저장해 둔 줄 배치)를 지운다 — 남겨 두면 빈 칸 때의 한 줄 배치로
    그려 긴 글이 겹친다(2026-10-28 제보: 입찰참가신청서 주소 칸). 지우면 한글이 열 때 다시 계산한다."""
    # 글을 넣은 그 문단(가장 안쪽)만 — 표를 담은 바깥 문단까지 지우면 한글이 표 전체를 다시 계산해 원본보다 길어져
    # 한 쪽짜리 서식이 두 쪽으로 넘어갔음(2026-10-28 제보). 바깥 문단·표 크기는 원본 계산값 그대로 둔다
    def one(m):
        p = m.group(0)
        if MARK not in p: return p
        # 한 줄짜리 문단이고 넣은 글이 그 줄 폭 안에 들어가면 원본 줄 배치를 그대로 둠 — 지우면 한글이 다시 계산하면서 줄 간격이
        # 조금씩 늘어, 쪽 전체가 표 하나인 서식이 두 쪽으로 넘어감(2026-10-28 경상남도 각서). 넘치는 문단만 지워 다시 줄바꿈하게
        segs = re.findall(r'<hp:lineseg\b[^>]*>', p)
        hz = re.search(r'horzsize="(\d+)"', segs[0]) if len(segs) == 1 else None
        cid = re.search(r'charPrIDRef="(\d+)"', p)
        em = CHAR.get(cid.group(1), (1000, 0))[0] if cid else 1000
        if hz and width(text(p).replace(MARK, '').rstrip()) * em / 2 <= int(hz.group(1)) * 0.97:
            return p
        return re.sub(r'<hp:linesegarray>.*?</hp:linesegarray>', '', p, flags=re.S)
    xml = re.sub(r'<hp:p\b(?:(?!<hp:p\b).)*?</hp:p>', one, xml, flags=re.S)
    return xml.replace(MARK, '')


def put_text(cell, value):
    """빈 칸의 첫 run에 글을 넣는다(글자 모양은 그 칸 것 그대로)."""
    value = value + MARK
    m = re.search(r'<hp:run\b([^>]*)/>', cell)
    if m:
        return cell[:m.start()] + f'<hp:run{m.group(1)}><hp:t>{value}</hp:t></hp:run>' + cell[m.end():]
    m = re.search(r'(<hp:run\b[^>]*>)(.*?)(</hp:run>)', cell, flags=re.S)
    if m:
        inner = re.sub(r'<hp:t\s*/>|<hp:t(?:\s[^>]*)?>\s*</hp:t>', '', m.group(2))
        return cell[:m.start()] + m.group(1) + inner + f'<hp:t>{value}</hp:t>' + m.group(3) + cell[m.end():]
    return None


STRIKE = set()  # 취소선이 걸린 글자 모양 번호 — 파일마다 header.xml에서 채움
CHAR = {}       # 글자 모양 번호 → (크기, 한글 자간 %) — 파일마다 채움


def loosen(cell):
    """자간을 많이 좁힌 글자 모양(−10% 넘게 — 유아숲 입찰건명 칸 −31%로 글자가 겹침)을 같은 크기·보통 자간 모양으로.
    한 줄에 안 들어가면 한글이 두 줄로 넘긴다(글을 넣은 문단은 줄 배치 정보를 지우므로)."""
    def swap(m):
        cid = m.group(1)
        if cid not in CHAR or CHAR[cid][1] >= -10: return m.group(0)
        h = CHAR[cid][0]
        same = [k for k, (hh, sp) in CHAR.items() if hh == h and -5 <= sp <= 5 and k not in STRIKE]
        return f'charPrIDRef="{same[0]}"' if same else m.group(0)
    return re.sub(r'charPrIDRef="(\d+)"', swap, cell)


def no_strike(cell, table):
    """빈 칸에 미리 지정된 글자 모양이 취소선이면(발주처 원본에 남은 모양 — 2026-10-28 제보: 제안업체 현황 주소),
    같은 표에서 글이 있는 칸 중 취소선 없는 가장 흔한 모양으로 바꾼다."""
    ids = re.findall(r'charPrIDRef="(\d+)"', cell)
    if not any(i in STRIKE for i in ids): return cell
    used = [i for c in re.findall(r'<hp:run charPrIDRef="(\d+)"[^/>]*>\s*<hp:t>[^<]', table) for i in [c] if i not in STRIKE]
    if not used: return cell
    good = max(set(used), key=used.count)
    return re.sub(r'charPrIDRef="(\d+)"', lambda m: f'charPrIDRef="{good}"' if m.group(1) in STRIKE else m.group(0), cell)


def set_text(cell, value):
    """칸 글을 통째로 value로(빈칸 틀 글 "총     명" 같은 것을 지일 값으로 바꿀 때) — 첫 글 조각에 넣고 나머지는 비움."""
    ts = list(re.finditer(r'<hp:t>[^<]*</hp:t>', cell))
    if not ts: return put_text(cell, value)
    out, p = [], 0
    for n, m in enumerate(ts):
        out += [cell[p:m.start()], f'<hp:t>{value}{MARK}</hp:t>' if n == 0 else '<hp:t></hp:t>']; p = m.end()
    return ''.join(out) + cell[p:]


# 같은 뜻의 칸 이름 → 지일 작성판에서 모은 칸 이름(PROFILE 키) — 발주처마다 칸 이름이 달라도 저장해 둔 값을 씀
# (2026-10-28: 에코팜랜드 "회사설립연도"로 저장, 유아숲은 "설립일"이라 못 채웠음)
SYNONYM = [(r'(회사)?설립(연도|일|일자|년월일|년도)?', '회사설립연도'), (r'(FAX|팩스)(번호)?', 'FAX번호'),
           (r'(E-?mail|이메일|전자우편)(주소)?', 'E-Mail'), (r'홈페이지(주소)?', '홈페이지주소'),
           (r'사업자(등록)?번호', '사업자번호'), (r'(대표)?전화(번호)?', '전화번호'),
           (r'사업분야(\(사업자등록증표기\))?', '사업분야(사업자등록증표기)')]


def profile_of(label):
    label = re.sub(r'^\d{1,2}\.(?!\d)', '', label)  # "6.회사설립년도"
    if label in PROFILE: return PROFILE[label]
    for rx, key in SYNONYM:
        if re.fullmatch(rx, label, flags=re.I) and key in PROFILE: return PROFILE[key]
    return None


def date_into(template, value):
    """칸의 날짜 틀("     년     월", "  .  .")에 저장한 날짜("2009. 09. 18.")의 숫자를 간격 그대로 넣음. 틀이 아니면 None"""
    nums = re.findall(r'\d+', value)
    if len(nums) < 2 or not re.search(r'년|\.\s*\.', template) or re.search(r'\d', template): return None
    out, k = template, 0
    for unit in ('년', '월', '일'):
        if unit in out and k < len(nums):
            out = re.sub(rf'(\s*){unit}', lambda m: m.group(1)[:-len(nums[k]) or None] + nums[k] + unit if len(m.group(1)) > len(nums[k]) else m.group(1) + nums[k] + unit, out, count=1)
            k += 1
    return out


def similar_title(a, b):
    """띄어쓰기·괄호·'(가칭)'을 빼고 글자 2개짜리 조각이 70% 넘게 겹치면 같은 사업명으로 봄"""
    sq = lambda s: re.sub(r'[\s()（）「」『』]|가칭', '', s)
    ga, gb = sq(a), sq(b)
    g = lambda s: {s[i:i + 2] for i in range(len(s) - 1)}
    return bool(g(ga)) and len(g(ga) & g(gb)) / min(len(g(ga)), len(g(gb))) >= 0.7


def career(template):
    """'년 월 ~ 2025년 월 공고일 현재( 년 개월)' — 설립일부터 입찰일까지 사업기간."""
    est = re.findall(r'\d+', (PROFILE.get('회사설립연도') or {}).get('지일 값', ''))
    if len(est) < 2 or not BID_DATE: return None
    ey, em = int(est[0]), int(est[1]); y, m, _ = map(int, BID_DATE.split('-'))
    months = (y - ey) * 12 + (m - em)
    return f'{ey}년 {em:02d}월 ~ {y}년 {m:02d}월 공고일 현재( {months // 12}년 {months % 12}개월)'


def fill_tables(xml, log):
    # 안에 다른 표가 없는 표부터(이름 칸·값 칸은 대부분 가장 안쪽 표)
    out, pos = [], 0
    for tm in re.finditer(r'<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>', xml, flags=re.S):
        tbl = tm.group(0)
        cells = []
        for cm in re.finditer(r'<hp:tc\b.*?</hp:tc>', tbl, flags=re.S):
            a = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', cm.group(0))
            sp = re.search(r'<hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"', cm.group(0))
            if a: cells.append([int(a.group(2)), int(a.group(1)), int(sp.group(1)) if sp else 1, cm.start(), cm.end(), cm.group(0),
                                int(sp.group(2)) if sp else 1])
        at = {(x[0], x[1]): x for x in cells}
        edits = {}
        # 대리인·수임인 칸이 걸친 줄은 그 사람 개인 정보 칸이라 안 채움(2026-10-28: 유아숲 위임장 대리인 칸에 대표자 정보가 들어갔음)
        agent_rows = {rr for x in cells if re.search(r'대리인|수임인|수임자|피위임|위임받는', re.sub(r'\s+', '', text(x[5])))
                      for rr in range(x[0], x[0] + x[6])}
        # "대표자"로 묶인 칸(여러 줄 병합된 이름표) 안의 줄 — 그 안의 "성명"은 대표자 이름(2026-10-28 유아숲 위임장)
        ceo_group = {rr for x in cells if re.sub(r'\s+', '', text(x[5])) in ('대표자', '위임자') and x[6] > 1
                     for rr in range(x[0], x[0] + x[6])}
        # 증명서 서식이면 "신청인"으로 묶인 줄만
        applicant_rows = {rr for x in cells if re.sub(r'\s+', '', text(x[5])) in ('신청인', '신청자', '의뢰인', '신청업체', '제출자')
                          for rr in range(x[0], x[0] + x[6])}
        # 주민등록번호는 같은 줄에 대표자 칸이 있거나 대표자 묶음 안일 때만(대표자 줄의 주민번호)
        ceo_rows = {x[0] for x in cells if key_of(text(x[5])) == 'ceo'} | ceo_group
        for r, c, cs, s, e, body, rs in cells:
            label = re.sub(r'\s+', '', text(body))
            k = key_of(text(body))
            if not k and label == '성명' and r in ceo_group:
                k = 'ceo'
            # 입찰일자 칸 — 나라장터 입찰 마감일. 칸의 날짜 틀("2025년   월   일")은 간격 그대로 숫자만
            if label in ('입찰일자', '입찰일', '입찰서제출일') and BID_DATE:
                nxt = at.get((r, c + cs))
                if nxt and nxt[3] not in edits:
                    y, mo, d = BID_DATE.split('-')
                    body2 = re.sub(r'(<hp:t>)([^<]*?)(\d{4})?(\s*)년(\s+)월(\s+)일', lambda m: f'{m.group(1)}{m.group(2)}{m.group(3) or y}{m.group(4)}년{m.group(5)}{mo}월{m.group(6)}{d}일{MARK}', nxt[5], count=1)
                    if body2 == nxt[5] and not text(nxt[5]):
                        body2 = put_text(nxt[5], f'{y}년 {mo}월 {d}일')
                    if body2 and body2 != nxt[5]:
                        edits[nxt[3]] = (nxt[4], body2); log.append(f'표 입찰일자 → {BID_DATE}')
                continue
            prof = None if MODE.get('staff') else profile_of(label)
            # 해당부문 사업기간은 빈칸(2026-10-28 사용자 결정 — 발주처마다 기준일이 달라 직접 작성). career()는 남겨 둠
            if not k and not prof: continue
            if r in agent_rows: continue
            if MODE['cert'] and r not in applicant_rows: continue
            if k == 'rrn' and r not in ceo_rows: continue
            nxt = at.get((r, c + cs))
            # 오른쪽 칸이 아주 좁으면 값 칸이 아니라 상자 사이 간격(2026-10-28 유아숲 조직도 — 간격 칸에 ㈜지일이 들어갔음)
            nw = re.search(r'<hp:cellSz width="(\d+)"', nxt[5]) if nxt else None
            if nw and int(nw.group(1)) < 2500:
                nxt = None
            if nxt and nxt[3] not in edits:
                cur = re.sub(r'\s+', '', text(nxt[5]))
                new = value = None
                if prof and cur == re.sub(r'\s+', '', prof['빈칸']):  # 지일이 쓴 값 그대로
                    value = prof['지일 값']; new = set_text(nxt[5], value) if cur else put_text(nxt[5], value)
                elif prof and cur and date_into(text(nxt[5]), prof['지일 값']):  # 날짜 틀("년 월")에 저장한 날짜 숫자만
                    value = date_into(text(nxt[5]), prof['지일 값']); new = set_text(nxt[5], value)
                elif k and not cur:
                    value = JIIL[k]; new = put_text(nxt[5], value)
                elif k == 'ceo' and cur in ('(인)', '(인감)', '(서명)', '(서명또는인)'):
                    # 대표자 칸에 "(인)"만 있으면 그 앞에 이름(2026-10-28 유아숲 제안서 제출서 — 지일 작성판도 "대표자 (인)")
                    value = JIIL['ceo']; new = set_text(nxt[5], f"{value}  {text(nxt[5]).strip()}")
                elif k == 'name' and re.match(r'대표자[:：]', cur):
                    # 한 칸에 업체명과 "대표자: (인)"을 같이 적는 서식(2026-11-05 서면질의서) — 앞에 업체명, 대표자는 줄 규칙이 넣음
                    value = JIIL['name']; new = nxt[5].replace('<hp:t>', f'<hp:t>{value}{MARK}     ', 1)
                elif k == 'title' and cur and TITLE and similar_title(cur, TITLE):
                    # 원본에 사업명이 이미 있으면(띄어쓰기 없이 좁혀 넣은 것) 나라장터 공고명(띄어쓰기 됨)으로 바꿈
                    value = TITLE; new = set_text(nxt[5], value)
                if new:
                    edits[nxt[3]] = (nxt[4], loosen(no_strike(new, tbl))); log.append(f'표 {label} → {value}')
        # 글을 넣은 칸 중 오른쪽 정렬인 칸은, 같은 표에서 글을 넣은 다른 칸의 정렬로 맞춤(유아숲 위임장 업체명만 오른쪽으로 붙었음)
        aligns = dict(re.findall(r'<hh:paraPr id="(\d+)"[^>]*>\s*<hh:align horizontal="(\w+)"', HEADER['xml']))
        filled_pp = [re.search(r'paraPrIDRef="(\d+)"', v[1]).group(1) for v in edits.values()
                     if MARK in v[1] and re.search(r'paraPrIDRef="(\d+)"', v[1])]
        other = [p for p in filled_pp if aligns.get(p) != 'RIGHT']
        if other:
            good = max(set(other), key=other.count)
            for s0, (e0, v) in list(edits.items()):
                m = re.search(r'paraPrIDRef="(\d+)"', v)
                if MARK in v and m and aligns.get(m.group(1)) == 'RIGHT' and '(인' not in text(v):  # "대표자 (인)" 칸은 오른쪽 그대로
                    edits[s0] = (e0, v.replace(f'paraPrIDRef="{m.group(1)}"', f'paraPrIDRef="{good}"'))
                    log.append('오른쪽 정렬 칸 맞춤')
                # 오른쪽에 값 칸이 있는 이름 칸 — 줄 규칙이 이름 칸에 또 넣지 않게 표시(끝에 지움)
                # 단, 글이 짧은 이름표 칸만 — 서명란까지 든 큰 칸에 붙으면 그 안 줄이 통째로 건너뛰어짐(에코팜랜드 신청서)
                if s not in edits and len(text(body)) <= 30: edits[s] = (e, body.replace('<hp:tc', '<hp:tc jfskip="1"', 1))
        if edits:
            buf, p = [], 0
            for s in sorted(edits):
                e, new = edits[s]; buf += [tbl[p:s], new]; p = e
            tbl = ''.join(buf) + tbl[p:]
        out += [xml[pos:tm.start()], tbl]; pos = tm.end()
    return ''.join(out) + xml[pos:]


# 주민등록번호는 줄 규칙에서 뺌 — "주민등록번호 :" 줄은 대리인·동의자 칸이라(지일 작성판도 비움). 대표자 칸이 있는 표에서만 넣는다
LBL = r'(업\s*체\s*명|대\s*표\s*자|소\s*속|신\s*청\s*인|(?:입\s*찰\s*건|사\s*업|용\s*역|과\s*업|공\s*사)\s*명|제안업체\s*명|제안업체\s*대표|사업체\s*명|회\s*사\s*명|상\s*호|주\s*소(?:\s*\(\s*소재지\s*\))?|소\s*재\s*지|사\s*업\s*자\s*(?:등\s*록\s*)?번\s*호|법\s*인\s*(?:등\s*록\s*)?번\s*호|서\s*약\s*자|T\s*E\s*L|F\s*A\s*X|전\s*화\s*번\s*호|기\s*관\s*명)'
GAP = r'(?:\s|<hp:tab\b[^>]*/>)*'  # 이름과 쌍점 사이 띄어쓰기·탭
# ① "이름 : " 뒤가 비었거나 (인)만 — 글 조각 안 어디든  ② 글 조각 전체가 "이름 (인)"뿐(쌍점 없음)
LINE = re.compile(LBL + '(' + GAP + r'[:：])(\s*)(?=(?:(?:\(?인\)?|\(\s*대표사\s*\))\s*)?(?:<|$))')  # 괄호 없는 "인"도(도로교통공단 서약서), 표지 "업체명: (대표사)\"
# 뒤에 (인)이나 도장 표시 글자(한글 사용자 정의 기호 — 지일 작성판 "신청인 ㈜지일 󰂙")만 있는 줄
# 쌍점 없는 줄은 뒤에 (인)·도장 표시가 있거나, 앞에 빈칸이 많은 서명란 줄("          사업체명       ")일 때만 —
# 표 이름표 칸("상 호", "회사명")에 값이 붙던 것 막음(2026-10-28 유아숲 검수)
ONLY = re.compile(r'^(\s*)' + LBL + '(' + GAP + r')(\s*(?:\(인\)|[-\U000f0000-\U000fffff])\s*)$')
ONLY_SIG = re.compile(r'^(\s{5,})' + LBL + '(' + GAP + r')(\s*)$')


def width(s):
    """대략의 줄 너비 — 빈칸·영숫자 1, 한글·전각 글자 2"""
    return sum(2 if ord(ch) > 0x1100 else 1 for ch in re.sub(r'<[^>]+>', '', s))


PARA = {'room': None, 'em': 1000}  # 지금 고치는 문단의 폭(HWPUNIT)·글자 크기 — fill_lines가 문단마다 채움


def shrink(spaces, value, line=''):
    """값을 넣으면 줄이 문단 폭을 넘칠 때만 넘치는 만큼 뒤 빈칸을 줄임 — 그대로 두면 "(인)"이 다음 줄로 밀림
    (2026-10-28 유아숲 입찰참가신청서 신청인 줄). 폭은 원본 줄 배치 정보(horzsize), 글자 너비는 한글 1em·빈칸 0.5em으로 셈
    (유아숲 줄로 맞춰 봄). 넘치지 않는 줄(에코팜랜드 서약서·보안각서)은 빈칸 그대로."""
    # (2026-10-28 사용자 요청으로 바꿈) 넘칠 때만이 아니라 늘 — 넣은 글자 너비만큼 뒤 빈칸을 줄여 "(인)"이 원래 자리에 남게
    # (위임장 대표자 줄의 (인)이 대리인 줄 (인)과 같은 자리). 한글·전각 1자 = 빈칸 2칸
    if spaces.strip(' ') != '' or len(spaces) <= 1:
        return spaces
    return ' ' * max(1, len(spaces) - (1 + width(value)))


def fix_title(xml, log):
    """서식 글 속 사업명이 띄어쓰기만 다르면("(가칭)전라남도교육청유아숲놀이체험원…") 나라장터 공고명(띄어쓰기 됨)으로 —
    글 조각(run)이 나뉘어 있어도 한 문단 안이면 바꿈(앞 조각의 글자 모양을 씀). 2026-10-28 유아숲 제안서 제출서."""
    if not TITLE: return xml
    sq = re.sub(r'\s+', '', TITLE)
    if len(sq) < 8: return xml
    sep = r'(?:\s|</hp:t>(?:</hp:run><hp:run\b[^>]*>)?<hp:t>)*'
    pat = re.compile(sep.join(re.escape(ch) for ch in sq))
    xml, n = pat.subn(lambda m: html_escape(TITLE) + MARK if m.group(0) != html_escape(TITLE) else m.group(0), xml)
    if n: log.append(f'사업명 띄어쓰기 ×{n}')
    return xml


def html_escape(s):
    return html.escape(s, quote=False)


def fill_lines(xml, log):
    xml = fix_title(xml, log)
    def rep(mm):
        k = key_of(mm.group(1))
        if not k or (MODE.get('agent') and re.sub(r'\s', '', mm.group(1)) == '소속'): return mm.group(0)  # 위임장 "소 속:"은 대리인 줄
        log.append(f'줄 {re.sub(r"\s", "", mm.group(1))} → {JIIL[k]}')
        # 지일 작성판처럼 쌍점 뒤 한 칸 띄우고 값, 원래 빈칸은 값 뒤에 남김("대 표 자 : 대표자        (인)")
        return mm.group(1) + mm.group(2) + ' ' + JIIL[k] + MARK + shrink(mm.group(3), JIIL[k], mm.string)

    def one(m):
        body = m.group(2)
        # 입찰공고번호 칸의 "제         호" — 글 조각 전체가 이 모양일 때만
        if NOTICE_NO and re.fullmatch(r'\s*제\s{2,}호\s*', body):
            log.append(f'공고번호 → {NOTICE_NO}')
            return m.group(1) + f'제 {NOTICE_NO} 호{MARK}' + m.group(3)
        # "TEL :          FAX : " 한 줄(2026-10-29 도로교통공단 일반현황)
        tf = re.fullmatch(r'(\s*TEL\s*[:：])(\s+)(FAX\s*[:：])(\s*)', body)
        # 표지 "전화번호:        (FAX:        )" 한 줄(2026-11-05 국립아시아문화전당 사업수행능력평가서 표지 — 사용자: 전화번호까지 채울 것)
        pf = re.fullmatch(r'(\s*전\s*화\s*번\s*호\s*[:：])(\s*)(\(?\s*F\s*A\s*X\s*[:：])(\s*)(\)?\s*)', body)
        if pf and JIIL.get('tel'):
            log.append(f'줄 전화번호·FAX → {JIIL["tel"]} · {JIIL.get("fax", "")}')
            return (m.group(1) + pf.group(1) + ' ' + JIIL['tel'] + MARK + shrink(pf.group(2), JIIL['tel']) + pf.group(3) + ' '
                    + JIIL.get('fax', '') + MARK + shrink(pf.group(4), JIIL.get('fax', '')) + pf.group(5) + m.group(3))
        if tf:
            log.append(f'줄 TEL·FAX → {JIIL["tel"]} · {JIIL["fax"]}')
            return (m.group(1) + tf.group(1) + ' ' + JIIL['tel'] + MARK + shrink(tf.group(2), JIIL['tel']) + tf.group(3) + ' '
                    + JIIL['fax'] + MARK + m.group(3))
        if LINE.search(body):
            body = LINE.sub(rep, body)
        else:
            om = ONLY.match(body) or ONLY_SIG.match(body)
            # 서약자는 "(인)"이 같은 줄에 있을 때만 — 머리 줄 "서 약 자" 아래에 업체명·대표자 줄이 따로 있는 서식(경상남도 청렴서약서)
            if om and key_of(om.group(2)) == 'sig' and not ONLY.match(body): om = None
            if om and key_of(om.group(2)):
                k = key_of(om.group(2)); log.append(f'줄 {re.sub(r"\s", "", om.group(2))} → {JIIL[k]}')
                # 이름표 바로 뒤 한 칸에 값, 원래 빈칸은 값 뒤로("신청인 ㈜지일          󰂙")
                gap = om.group(3)
                if '<' not in gap: gap = shrink(gap, JIIL[k], body)
                body = om.group(1) + om.group(2) + ' ' + JIIL[k] + MARK + gap + om.group(4)
        return m.group(1) + body + m.group(3)
    skip_spans = [(m.start(), m.end()) for m in re.finditer(r'<hp:tc jfskip="1".*?</hp:tc>', xml, flags=re.S)]

    def para(pm):
        # 문단마다 폭(줄 배치 정보의 horzsize)과 글자 크기를 잡아 두고 그 안의 글 조각을 고친다
        p = pm.group(0)
        if any(a <= pm.start() < b for a, b in skip_spans):  # 표에서 이미 채운 이름표 칸
            return p
        hz = re.search(r'horzsize="(\d+)"', p)
        cid = re.search(r'charPrIDRef="(\d+)"', p)
        PARA['room'] = int(hz.group(1)) if hz else None
        PARA['em'] = CHAR.get(cid.group(1), (1000, 0))[0] if cid else 1000
        return re.sub(r'(<hp:t>)((?:(?!</hp:t>).)*?)(</hp:t>)', one, p, flags=re.S)

    # 가장 안쪽 문단마다(표시한 이름 칸 jfskip 안은 건너뜀), 끝나면 표시를 지움
    xml = re.sub(r'<hp:p\b(?:(?!<hp:p\b).)*?</hp:p>', para, xml, flags=re.S)
    xml = xml.replace('<hp:tc jfskip="1"', '<hp:tc')
    # 서약서 "서약자 ○○○회사 대표 ○○○(인)"(2026-10-29 제주 청렴계약서)
    xml, n = re.subn(r'○{3,}\s*(업체|회사)(\s*대표\s*)○{3,}', JIIL['name'] + r'\g<2>' + JIIL['ceo'] + MARK + ' ', xml)
    if n: log.append(f'자리표시 ○○○회사 대표 ○○○ → {JIIL["sig"]} ×{n}')
    # 위임장 "본인은 ○○○○○업체의 대표로서" 같은 자리 표시
    xml, n = re.subn(r'○{3,}\s*(업체|회사)(?=\s*의)', JIIL['name'] + MARK + ' ', xml)
    if n: log.append(f'자리표시 ○○○업체 → {JIIL["name"]} ×{n}')
    return xml


HEADER = {'xml': ''}  # 지금 고치는 파일의 header.xml — 정렬을 바꾼 문단 모양(paraPr)을 여기에 더함
LABEL_WORDS = ['주소', '상호', '성명', '업체명', '대표자', '회사명', '소속', '직위', '소재지', '사업자번호', '전화번호', '생년월일',
               '주민등록번호', '주민(법인)등록번호', '법인등록번호', '제안업체명', '제안업체대표', '사업체명', '서약자', '동의자', '대리인']
LABEL_LINE = re.compile(r'^\s*(?:' + '|'.join(r'\s*'.join(re.escape(c) for c in w) for w in LABEL_WORDS) + r')\s*[:：]')


def align_blocks(xml, log):
    """가운데 정렬된 이름표 줄 묶음("주 소 : …" / "상 호 : …" / "성 명 : …")을 왼쪽 정렬 + 같은 들여쓰기로 — 가운데 정렬이면
    값 길이가 줄마다 달라 이름표 시작 위치가 들쭉날쭉해짐(2026-10-28 유아숲 사용인감계). 가장 긴 줄이 원래 가운데 있던 자리에 오게."""
    head = HEADER['xml']
    align = dict(re.findall(r'<hh:paraPr id="(\d+)"[^>]*>\s*<hh:align horizontal="(\w+)"', head))
    paras = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r'<hp:p\b(?:(?!<hp:p\b).)*?</hp:p>', xml, flags=re.S)]
    info = []
    for s, e, p in paras:
        pid = re.search(r'paraPrIDRef="(\d+)"', p).group(1)
        t = text(p).replace(MARK, '')
        info.append((s, e, p, pid, t, align.get(pid) == 'CENTER' and bool(LABEL_LINE.match(t))))
    groups, cur = [], []
    for k, row in enumerate(info):
        if row[5] and (not cur or cur[-1] == k - 1): cur.append(k)
        else:
            if len(cur) >= 2: groups.append(cur)
            cur = [k] if row[5] else []
    if len(cur) >= 2: groups.append(cur)
    if not groups: return xml
    next_id = max(int(i) for i in re.findall(r'<hh:paraPr id="(\d+)"', head)) + 1
    made, edits = {}, {}
    for g in groups:
        hz = next((int(m.group(1)) for k in g for m in [re.search(r'horzsize="(\d+)"', info[k][2])] if m), None)
        if not hz: continue
        def w_of(k):
            cid = re.search(r'charPrIDRef="(\d+)"', info[k][2])
            em = CHAR.get(cid.group(1), (1000, 0))[0] if cid else 1000
            return width(info[k][4].strip()) * em / 2  # 앞 빈칸은 지울 것이라 뺌
        indent = max(0, int((hz - max(w_of(k) for k in g)) / 2))
        for k in g:
            pid = info[k][3]
            if (pid, indent) not in made:
                src = re.search(rf'<hh:paraPr id="{pid}".*?</hh:paraPr>', head, flags=re.S).group(0)
                new = re.sub(r'<hh:paraPr id="\d+"', f'<hh:paraPr id="{next_id}"', src, count=1)
                new = new.replace('horizontal="CENTER"', 'horizontal="LEFT"', 1)
                new = re.sub(r'(<hp:case\b.*?<hc:left value=")(-?\d+)', lambda m: m.group(1) + str(int(m.group(2)) + indent // 2), new, count=1, flags=re.S)
                new = re.sub(r'(<hp:default>.*?<hc:left value=")(-?\d+)', lambda m: m.group(1) + str(int(m.group(2)) + indent), new, count=1, flags=re.S)
                if '<hp:switch>' not in new:  # 분기 없는 모양
                    new = re.sub(r'(<hc:left value=")(-?\d+)', lambda m: m.group(1) + str(int(m.group(2)) + indent), new, count=1)
                head = head.replace('</hh:paraProperties>', new + '</hh:paraProperties>', 1)
                made[(pid, indent)] = str(next_id); next_id += 1
            s, e, p = info[k][0], info[k][1], info[k][2]
            p2 = re.sub(r'paraPrIDRef="\d+"', f'paraPrIDRef="{made[(pid, indent)]}"', p, count=1)
            # 가운데 정렬 때 줄을 맞추려 넣어 둔 앞 빈칸은 지움(경상남도 각서 "                 성       명 :")
            p2 = re.sub(r'<hp:t>\s+', '<hp:t>', p2, count=1)
            # 줄 배치 정보는 두고 시작 위치(horzpos)·폭만 새 들여쓰기에 맞춤 — 지우면 줄 간격이 다시 계산돼 쪽이 넘어갈 수 있음
            p2 = re.sub(r'horzpos="(\d+)" horzsize="(\d+)"',
                        lambda m: f'horzpos="{int(m.group(1)) + indent}" horzsize="{max(1000, int(m.group(2)) - indent)}"', p2)
            edits[s] = (e, p2)
        log.append(f'정렬 맞춤 {len(g)}줄')
    head = re.sub(r'(<hh:paraProperties itemCnt=")(\d+)', lambda m: m.group(1) + str(int(m.group(2)) + len(made)), head, count=1)
    HEADER['xml'] = head
    out, pos = [], 0
    for s in sorted(edits):
        e, p2 = edits[s]; out += [xml[pos:s], p2]; pos = e
    return ''.join(out) + xml[pos:]


TAB_W = 4000  # 한글 기본 탭 간격(HWPUNIT) — 재직증명서 서명란 탭 6개가 쪽 폭의 절반쯤이라 맞음


def fix_tabs(xml, log):
    """앞에 탭을 여러 개 넣어 오른쪽에서 시작하는 서명란 줄 묶음("⇥⇥⇥⇥⇥⇥주 소 : …")에 긴 값을 넣어 줄이 넘치면,
    묶음 전체의 앞 탭을 똑같이 줄여 한 줄에 들어가게(2026-10-28 유아숲 재직증명서 — 주소가 넘쳐 양쪽 정렬로 글자가 흩어짐)."""
    # 앞을 빈칸으로 민 서명란("                                     주  소: …", 경상남도 청렴서약서)도 같은 방식 — 앞 빈칸을 똑같이 줄임
    paras = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r'<hp:p\b(?:(?!<hp:p\b).)*?</hp:p>', xml, flags=re.S)]
    LEAD = re.compile(r'(<hp:t>)((?:<hp:tab\b[^>]*/>)*)( *)')
    rows = []
    for s, e, p in paras:
        m = LEAD.search(p)
        first = m is not None and p.find('<hp:t>') == m.start()
        tabs = len(re.findall(r'<hp:tab\b', m.group(2))) if first else 0
        sps = len(m.group(3)) if first else 0
        t = text(p).replace(MARK, '')
        rows.append((s, e, p, tabs, t, (tabs >= 2 or sps >= 6) and bool(LABEL_LINE.match(t.strip())), sps))
    groups, cur = [], []
    for k, r in enumerate(rows):
        if r[5] and (not cur or cur[-1] == k - 1): cur.append(k)
        else:
            if cur: groups.append(cur)
            cur = [k] if r[5] else []
    if cur: groups.append(cur)
    edits = {}
    for g in groups:
        room = next((int(m.group(1)) for k in g for m in [re.search(r'horzsize="(\d+)"', rows[k][2])] if m), None)
        if not room: continue
        def em_of(k):
            cid = re.search(r'charPrIDRef="(\d+)"', rows[k][2])
            return CHAR.get(cid.group(1), (1000, 0))[0] if cid else 1000
        def need(k):
            return rows[k][3] * TAB_W + (rows[k][6] + width(rows[k][4].strip())) * em_of(k) / 2
        over = max(need(k) for k in g) - room * 0.97
        if over <= 0: continue
        use_tabs = all(rows[k][3] >= 2 for k in g)
        if use_tabs:
            cut = min(min(rows[k][3] for k in g) - 1, int(over // TAB_W) + 1)
        else:
            cut = min(min(rows[k][6] for k in g), int(over // (em_of(g[0]) / 2)) + 1)
        if cut <= 0: continue
        for k in g:
            s, e, p = rows[k][0], rows[k][1], rows[k][2]
            if use_tabs:
                p2 = LEAD.sub(lambda m: m.group(1) + MARK + ''.join(re.findall(r'<hp:tab\b[^>]*/>', m.group(2))[cut:]) + m.group(3), p, count=1)
            else:
                p2 = LEAD.sub(lambda m: m.group(1) + MARK + m.group(2) + m.group(3)[cut:], p, count=1)
            edits[s] = (e, p2)
        log.append(f'앞 {"탭" if use_tabs else "빈칸"} {cut}개 줄임({len(g)}줄)')
    out, pos = [], 0
    for s in sorted(edits):
        e, p2 = edits[s]; out += [xml[pos:s], p2]; pos = e
    return ''.join(out) + xml[pos:]


# 지일이 채우지 않는 서식 — 여러 회사가 쓰는 것(공동수급·합의각서·대표자 선임계)
SKIP = re.compile(r'공동수급|합의각서|선임계')
# 직원 개인 정보를 적는 인력 서식 — 소속 말고 저장해 둔 칸(메일 등)은 넣지 않음(직원 칸은 비움, 2026-10-28 사용자 결정 · 11/05 이력사항에 회사 메일이 들어가 추가)
STAFF = re.compile(r'참여인력|이력사항|이력서|인력\s*투입|기술인력|경력\s*증명')
# 발주처가 증명해 주는 서식 — 표의 "신청인" 칸만 채우고 증명기관 칸·서명 줄은 비움(2026-10-28 사용자: 유아숲 수행실적증명서 신청인 정보)
CERT = re.compile(r'실적\s*증명|증명원|실적\s*확인서')
MODE = {'cert': False}


def fill_form(src: Path, dst: Path, company: dict, bid_date: str | None = None, title: str | None = None,
              notice_no: str | None = None) -> list[str]:
    """서식 한 장을 채워 `dst`에. 넣은 칸 목록을 돌려준다(여러 회사·남이 발급하는 서식은 안 채움)."""
    _configure(company, bid_date, title, notice_no)
    log: list[str] = []
    with zipfile.ZipFile(src) as z:
        skip = bool(SKIP.search(src.name))
        head = z.read("Contents/header.xml").decode("utf-8")
        CHAR.clear()
        for m in re.finditer(r'<hh:charPr id="(\d+)" height="(\d+)"(?:(?!</hh:charPr>).)*?<hh:spacing hangul="(-?\d+)"', head, flags=re.S):
            CHAR[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        STRIKE.clear()
        STRIKE.update(m.group(1) for m in re.finditer(r'<hh:charPr id="(\d+)"(?:(?!</hh:charPr>).)*?<hh:strikeout shape="(?!NONE|3D)[A-Z_]+"', head, flags=re.S))
        HEADER["xml"] = head
        datas = {info.filename: z.read(info.filename) for info in z.infolist()}
        if not skip:
            MODE["cert"] = bool(CERT.search(src.name))
            MODE["agent"] = "위임" in src.name
            MODE["staff"] = bool(STAFF.search(src.name))
            for name in datas:
                if re.match(r"Contents/section\d+\.xml$", name):
                    x = datas[name].decode("utf-8")
                    x = fill_tables(x, log)
                    x = fix_title(x, log) if MODE["cert"] else fill_lines(x, log)  # 증명서는 서명 줄(증명기관) 안 채움
                    datas[name] = drop_layout(fix_tabs(align_blocks(x, log), log)).encode("utf-8")
            datas["Contents/header.xml"] = HEADER["xml"].encode("utf-8")  # 정렬을 바꾼 문단 모양이 더해진 header
        with zipfile.ZipFile(dst, "w") as w:
            for info in z.infolist():
                w.writestr(zipfile.ZipInfo(info.filename, info.date_time), datas[info.filename],
                           compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return log
