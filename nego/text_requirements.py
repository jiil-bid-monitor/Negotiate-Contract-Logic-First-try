"""첨부 참가자격 절에서 **코드 없이 글로만 적힌** 요건을 읽는다 (2026-09-30 사용자 요청).

과거 제안요청서·공고문 144건(참가자격 절을 찾은 것)을 조사해 보니 코드 없는 요건이 많았다:

- 분야 이름만 적은 업종 32건 — "산업디자인 전문회사(시각디자인, 제품디자인, 종합디자인 분야 중 하나 이상)"
- 품명만 적은 직접생산증명 29건 — "직접생산증명서(실물모형)", "〈물품분류번호 : 60121002〉"(8자리)
- 실적 요건 22건, 현장설명회 참가 필수 6건(2026-10-14부터 안 찾음 — 사용자 결정), 기술인력 보유 1건

앞의 둘은 지일 보유 목록과 **이름으로 대조해 판정**하고(`name_bundles`), 뒤의 셋은 판정하지 않고
**"확인 필요" 칩**으로만 보여준다(`flag_requirements`). 중소기업·소상공인 확인서는 지일이 보유해
무시한다(사용자 확인).

판정은 **아는 이름만** 한다 — 보유 목록·코드 이름 사전(codes.json, code_names.json)에 없는 업종명은
건드리지 않는다. 모르는 이름을 추측해서 "미달"로 만들면 오탈락이 되기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 한 항목 안 이름들을 "또는"으로 볼 신호. 없으면 이름마다 모두 필요.
_OR_HINT = re.compile(r"또는|혹은|중\s*(?:하나|1\s*개|한\s*가지)|어느\s*하나|이상\s*(?:을|를)?\s*(?:보유|등록|신고|소지)")
# 요건이 아닌 항목 — 공동수급 방법·실적·결격사유 설명 속 업종명은 판정하지 않는다
# "적격심사 평가대상 업종 및 평가비율" 표는 자격요건이 아니다(2026-10-01 고이분교: "종합 건축공사업 … 100%")
_NOT_REQUIREMENT = re.compile(r"공동수급|공동도급|공동이행|분담이행|대표사|구성원|실적|제재|부정당|하도급|평가\s*대상\s*업종|평가\s*비율")
# 대기업·중견기업 참여 제한 문장("대기업 및 중견기업 소프트웨어 사업자는 본 입찰에 참여할 수 없으며") 속 업종명은 요건이 아니다
# (2026-10-02 제보: 고흥분청문화박물관 실감콘텐츠 — 원문에 없는 "소프트웨어사업자(1470)"가 보유 확인으로 뜸).
# 진짜 요건 항목 안에 "※ … 대기업 참여는 불가" 주석으로 끼어 있는 경우가 많아 항목 전체가 아니라 그 문장만 뺀다.
_RESTRICTION_SENTENCE = re.compile(r"대기업|중견\s*기업|상호\s*출자")
_DESIGN_FIELDS = ("시각", "제품", "포장", "환경", "멀티미디어", "서비스", "종합")
_DESIGN_RE = re.compile(r"산업\s*디자인\s*전문\s*회사\s*[(\[]([^)\]]{1,80})[)\]]")
_DIRECT_RE = re.compile(r"직접\s*생산\s*(?:확인)?\s*증명(?:서)?")
_CLASS8_RE = re.compile(r"(?:물품\s*(?:분류)?\s*번호|분류\s*번호)\s*[:：]?\s*(\d{8})(?!\d)")


# 금지·제한 문장 속 업종명도 요건이 아니다 — "… 사업자는 참여할 수 없음", "참여를 제한", "해당하지 않는 자", "제외함"
# (2026-10-02 사용자 요청: 고흥 사례가 다른 표현으로 다시 생기지 않게). 요건 동사("등록한 업체")까지 요구하면 "1) ○○법에 따른
# 산업디자인전문회사 …"처럼 동사 없이 나열한 진짜 요건이 빠져서(과거 문서 9건) 금지·제한 쪽만 거른다.
_PROHIBITION = re.compile(
    r"참(?:여|가)\s*할\s*수\s*없|참(?:여|가)\s*(?:가|는|를|을)?\s*(?:불가|제한|금지)|입찰\s*참(?:여|가)\s*를?\s*제한"
    r"|해당\s*(?:하|되)지\s*(?:않|아니)|제외\s*(?:한다|함|됨)"
)


def _is_restriction(sent: str) -> bool:
    return bool(_RESTRICTION_SENTENCE.search(sent) or _PROHIBITION.search(sent))


def _drop_restriction_sentences(item: str) -> str:
    """제한·금지 문장만 빼고 이름을 찾는다. 그런 문장이 없으면 원문 그대로(줄 모양을 바꾸지 않으려고)."""
    sents = _sentences(item)
    if not any(_is_restriction(sent) for sent in sents):
        return item
    return "\n".join(sent for sent in sents if not _is_restriction(sent))


def _norm(text: str) -> str:
    return re.sub(r"[\s·ㆍ.]", "", text)


@dataclass(frozen=True)
class NameReq:
    label: str
    held: bool
    code: str | None = None


def _industry_dictionary(held_code_names: dict[str, str], lookup: dict[str, str]) -> dict[str, tuple[str, str | None]]:
    """정규화 이름 → (표시 이름, 코드). 업종(4자리)만. 보유 목록이 사전보다 우선.

    같은 이름이 여러 코드에 붙어 있으면(코드 사전의 "소프트웨어사업자" = 1426·1468·1469·1470, 분야별 등록) 코드를
    하나로 정할 수 없어 None으로 둔다 — 아무 코드나 붙이면 엉뚱한 분야를 요구하는 것처럼 보인다."""
    codes: dict[str, set[str]] = {}
    names: dict[str, str] = {}
    for source in (lookup, held_code_names):
        for code, name in source.items():
            if len(code) == 4 and name:
                codes.setdefault(_norm(name), set()).add(code)
                names[_norm(name)] = name
    # 보유 목록에 그 이름이 있으면 보유 코드로 ("실내건축공사업" = 사전 0006 · 등록증 4990 — 등록증 쪽)
    held_code = {_norm(name): code for code, name in held_code_names.items() if len(code) == 4 and name}
    return {k: (names[k], held_code.get(k) or (next(iter(c)) if len(c) == 1 else None)) for k, c in codes.items()}


def _is_held_name(name: str, held_names: set[str]) -> bool:
    """보유 이름과 완전히 같거나, 분야 없이 적은 통칭("소프트웨어사업자")이고 그 분야 중 하나를 보유
    ("소프트웨어사업자(컴퓨터관련서비스사업)")하면 보유."""
    key = _norm(name)
    return key in held_names or any(h.startswith(key + "(") for h in held_names)


def _design_names(item: str) -> list[str]:
    """"산업디자인 전문회사(시각디자인, 종합디자인 분야 중 하나 이상)" → 분야별 정식 이름들."""
    names = []
    for m in _DESIGN_RE.finditer(item):
        inside = m.group(1)
        for field in _DESIGN_FIELDS:
            if re.search(field + r"\s*(?:디자인)?", inside) and (field != "서비스" or "서비스디자인" in _norm(inside)):
                names.append(f"산업디자인전문회사({field}디자인분야)")
    return list(dict.fromkeys(names))


_SUBITEM_RE = re.compile(r"(?:(?<=\s)|^)(?=(?:\d{1,2}\)|[①-⑳])\s*)")


def sub_items(item: str) -> list[str]:
    """하위 번호 1)·2)·①로 나눈 조각들 (하위 번호가 없으면 항목 그대로 1개)."""
    return [p for p in _SUBITEM_RE.split(item) if p.strip()]


def name_bundles(
    item: str, held_code_names: dict[str, str], lookup: dict[str, str]
) -> list[list[NameReq]]:
    """코드 없는 항목에서 업종·직접생산 품목을 이름으로 찾아 [묶음(OR)] 목록(묶음끼리 AND)으로 돌려준다.

    한 항목 안에 "다음 각 조건을 모두 갖춘 업체 1) … 2) …"처럼 하위 번호가 있으면 하위 항목마다 따로
    본다(하위 항목끼리는 모두 필요). 찾은 게 없으면 빈 목록 — 판정하지 않는다.
    """
    parts = sub_items(item)
    if len(parts) > 1:
        return [b for p in parts for b in name_bundles(p, held_code_names, lookup)]
    if _NOT_REQUIREMENT.search(item):
        return []
    # 건너뛸 항목 판단은 원문으로 먼저 하고, 그다음 제한·금지 문장만 빼고 이름을 찾는다
    item = _drop_restriction_sentences(item)
    held_names = {_norm(n) for n in held_code_names.values()}
    held_products = {code: name for code, name in held_code_names.items() if len(code) == 10}
    found: list[NameReq] = []

    # ① 산업디자인전문회사 분야
    for name in _design_names(item):
        found.append(NameReq(name, _norm(name) in held_names))

    # ② 사전에 있는 업종명 (긴 이름부터 — "실내건축공사업" 안의 "건축공사업"을 따로 세지 않게)
    text = _norm(_DESIGN_RE.sub("", item))
    taken: list[tuple[int, int]] = []
    for key, (name, code) in sorted(_industry_dictionary(held_code_names, lookup).items(), key=lambda kv: -len(kv[0])):
        if len(key) < 4 or key.startswith("산업디자인전문회사"):
            continue
        for m in re.finditer(re.escape(key), text):
            if any(s < m.end() and m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            found.append(NameReq(f"{name}({code})" if code else name, _is_held_name(name, held_names), code))
            break

    # ③ 직접생산증명 품목 — 괄호 속 품명, 또는 8자리 물품분류번호(보유 10자리 세부품명번호의 앞자리)
    if _DIRECT_RE.search(item):
        for m in _CLASS8_RE.finditer(item):
            cls = m.group(1)
            held = any(code.startswith(cls) for code in held_products)
            label = next((name for code, name in held_products.items() if code.startswith(cls)), None)
            found.append(NameReq(f"{label or '물품분류'}({cls})", held, cls))
        if not _CLASS8_RE.search(item):
            for m in re.finditer(r"증명(?:서)?\s*[\[(<〔]+\s*(?:세부\s*품명\s*[:：]?\s*)?([가-힣A-Za-z]{2,20})", item):
                pname = m.group(1)
                if "번호" in pname or pname in ("세부품명", "물품", "품명"):
                    continue  # "(물품분류번호 …)" 같은 머리말은 품명이 아니다
                hit = next((name for name in held_products.values() if _norm(name).startswith(_norm(pname))), None)
                found.append(NameReq(pname, hit is not None))

    if not found:
        return []
    found = list({(r.label): r for r in found}.values())
    if len(found) > 1 and _OR_HINT.search(item):
        return [found]
    return [[r] for r in found]


# ── 판정하지 않고 칩으로만 보여주는 요건 ─────────────────────────────
_FLAGS = (
    # "최근 N년간 … X원 이상" 같은 실제 요건만 — 금액(또는 건수) 뒤에 "이상"이 와야 한다. 기간만 있는 평가 방법 설명
    # ("최근 3년간 업종별 실적에 … 합한 실적금액으로 평가합니다")은 요건이 아니다(2026-10-14 제보: 부산광역시 공고 제2026-2813호).
    ("실적", re.compile(r"실적|준공한|수행한\s*경험"),
     re.compile(r"\d[\d,.]*\s*(?:(?:억|천만|백만|만)\s*원?|원|건)[^.\n]{0,15}?이\s*상")),
    ("건축사사무소", re.compile(r"건축사\s*사무소"), re.compile(r"개설|등록|신고")),
    ("인력", re.compile(r"기술자|기술사|학예사|건축사|전문\s*인력|기사\s*자격"), re.compile(r"보유|소지|소속|재직|자격증")),
)


# 줄머리 표시(나./1)/①/-/※/❍ …)로 시작하는 줄은 새 문장이다
_LINE_HEAD = re.compile(r"\n\s*(?=[-※*·•□■○●◎❍◦▪▶►【]|\d{1,2}\s*[).]|[①-⑳]|[가-하]\s*[.)])")
_MAX_JOINED = 200
_FORM_RE = re.compile(r"서식\s*(?:제\s*)?\d")  # 이어 붙인 문장이 이보다 길면(공백 제외) 표·서식이 섞인 것 — 줄 단위로 되돌린다


def _sentences(item: str) -> list[str]:
    """항목을 문장으로 나눈다. PDF는 문장 중간에서 줄을 바꿔 "…단일공사 5억원 이상\n준공실적을 보유한 업체"가
    두 줄로 갈라진다(2026-10-01 제보: 영주 과수거점산지유통센터 — 실적 칩 누락). 줄바꿈은 줄머리 표시가 있을 때만
    문장 경계로 보고, 나머지는 이어 붙인 뒤 마침표로 나눈다. 너무 길어지면(표·서식 칸이 이어진 것) 줄 단위로 본다."""
    out = []
    for block in _LINE_HEAD.split(item):
        for sent in re.split(r"(?<=[.。])\s+(?=\S)", block):
            if len(re.sub(r"\s+", "", sent)) > _MAX_JOINED:
                out.extend(line for line in sent.split("\n") if line.strip())
            elif sent.strip():
                out.append(re.sub(r"\s*\n\s*", " ", sent))
    return out


# 실적 칩에서 뺄 문장 — "시공실적으로 입찰참가자격을 제한하지 아니한 입찰"(2026-10-01 제보: 부산 50+복합지원센터 인테리어)처럼
# 실적 제한이 없다는 안내, 수행능력평가·시공경험평가·평가대상 업종·평가비율·배점 설명(점수 산정이지 참가 요건이 아님)
_NOT_TRACK_REQUIREMENT = re.compile(
    r"제한\s*하지\s*(?:아니|않)|제한\s*(?:이\s*)?없|수행\s*능력\s*평가|시공\s*경험\s*평가|경영\s*상태\s*평가"
    r"|평가\s*대상\s*업종|평가\s*비율|배\s*점|평\s*점|가\s*점"
)

# 기술인력 칩에서 뺄 문장 — 평가 배점표("평가항목별 배점 … 전문인력 보유현황 (6점)", 2026-10-02 제보)는 점수 산정이지
# 인력을 갖춰야 참가할 수 있다는 요건이 아니다
_NOT_STAFF_REQUIREMENT = re.compile(r"배\s*점|평\s*가\s*항\s*목|평\s*점|가\s*점|\(\s*\d+(?:\.\d+)?\s*점\s*\)")

_LAW_TITLE_RE = re.compile(r"「[^」]{0,80}」")
_SUB_LINE = re.compile(r"^\s*[-·•]")  # "◦""▪"는 항목 머리표로도 써서(아리랑보부상로드 "◦ 공고일로부터 …") 하위 줄로 안 봄
_MAX_FLAG_TEXT = 220
_MAX_CONTEXT_TEXT = 500
_MAX_SIBLINGS = 4


def _with_context(sent: str, owner: dict) -> str:
    """칩 원문. "- 전시제작·설치 준공실적이 단일 건으로 5억원 이상"처럼 하위 줄이면 위의 머리 문장
    ("9) 다음 사항에 해당되는 업체(공고일 기준 최근 3년간…)")과 같은 머리 아래 다른 하위 줄도 줄바꿈으로 붙인다
    (2026-10-02 사용자 요청 — 남원 어린이과학체험관: 기간·두 번째 실적 조건이 팝업에서 빠져 보였음)."""
    flat = lambda t: re.sub(r"\s+", " ", t).strip()
    if not _SUB_LINE.match(sent) or sent not in owner:
        return flat(sent)[:_MAX_FLAG_TEXT]
    sents, i = owner[sent]
    head = i
    while head > 0 and _SUB_LINE.match(sents[head]):
        head -= 1
    if _SUB_LINE.match(sents[head]):
        return flat(sent)[:_MAX_FLAG_TEXT]  # 머리 문장이 없는 목록 — 예전처럼 한 줄만
    end = head + 1
    while end < len(sents) and _SUB_LINE.match(sents[end]):
        end += 1
    # 하위 줄이 많으면("○ 입찰참가자격: 각 호를 모두 충족" 아래 면허·실적·소재지 6줄 — 안성 고삼호수) 머리 문장 + 해당 줄만
    lines = sents[head:end] if end - head - 1 <= _MAX_SIBLINGS else [sents[head], sent]
    return "\n".join(flat(x) for x in lines)[:_MAX_CONTEXT_TEXT]


def flag_requirements(items: list[str]) -> list[dict]:
    """실적·기술인력·건축사사무소 요건을 찾아 [{kind, text}]로. 판정은 하지 않는다.
    현장설명회 참석 요건은 찾지 않는다(2026-10-14 사용자 결정 — 굳이 파싱할 필요 없음)."""
    out: dict[str, dict] = {}
    per_item = [_sentences(item) for item in items]
    sentences = [s for sents in per_item for s in sents]
    owner = {}  # 문장 → (그 항목의 문장 목록, 위치) — 하위 줄이면 머리 문장·형제 줄을 같이 보여주려고
    for sents in per_item:
        for i, sent in enumerate(sents):
            owner.setdefault(sent, (sents, i))
    for kind, subject, detail in _FLAGS:
        for s in sentences:
            if _FORM_RE.search(s):
                continue  # 제출 서식(참여인력 경력사항 표 등)의 칸 이름은 요건이 아니다
            if kind == "인력" and "건축사사무소" in out:
                break  # 건축사사무소 요건 문장의 "건축사 면허"를 인력 요건으로 또 세지 않는다
            if kind == "건축사사무소" and re.search(r"업종\s*코드|\[\d{4}\]|\(\d{4}\)", s):
                continue  # 코드 있는 업종과 "또는"으로 나열된 대안일 뿐 — 코드 판정이 이미 본다
            if kind == "실적" and _NOT_TRACK_REQUIREMENT.search(s):
                continue  # 실적으로 참가를 "제한하지 않는다"·적격심사 평가(배점) 설명은 참가 요건이 아니다
            if kind == "인력" and _NOT_STAFF_REQUIREMENT.search(s):
                continue  # 평가 배점표의 "전문인력 보유현황 (6점)" 같은 칸은 참가 요건이 아니다
            # 「…」 안 법령·지침 이름은 요건 낱말로 안 본다 — 「소프트웨어기술자 경력 및 소프트웨어사업자 실적 관리 지침」의 "기술자"가
            # 기술인력 칩으로 잡혔음(2026-10-14 제보: 컴퓨터관련서비스사업 신고 + 직접생산확인 8115169901 요건 — 자격 판정이 보는 내용)
            if subject.search(_LAW_TITLE_RE.sub(" ", s)) and detail.search(s):
                entry = {"kind": kind, "text": _with_context(s, owner)}
                out.setdefault(kind, entry)
                break
    for entry in out.values():
        entry["summary"] = summarize_flag(entry)
    return list(out.values())


_ZIP_PART_RE = re.compile(r"^=== \[압축 안\] (.+?) ===$", re.M)


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


def attach_sources(flags: list[dict], files: list[tuple[str, str]]) -> list[dict]:
    """칩 원문 문장이 어느 첨부파일에서 나왔는지 `source`(파일명)를 붙인다 (2026-10-02 사용자 요청 — 팝업 하단 출처).

    PDF·HWP는 문장 중간에서 줄을 바꾸므로 공백을 다 지우고 원문 앞 40자로 찾는다. 압축 첨부는 "=== [압축 안] 이름 ==="
    구분으로 나눠 "압축파일 › 안쪽 파일"로 적는다. 못 찾으면 source를 넣지 않는다(화면은 출처 줄을 안 그림)."""
    for flag in flags:
        needle = _squeeze(flag.get("text", ""))[:40]
        if not needle:
            continue
        for file_name, text in files:
            pieces = _ZIP_PART_RE.split(text)
            # split 결과: [압축 밖 앞부분, 이름1, 본문1, 이름2, 본문2, …]
            parts = [(file_name, pieces[0])] + [
                (f"{file_name} › {pieces[i]}", pieces[i + 1]) for i in range(1, len(pieces) - 1, 2)
            ]
            hit = next((label for label, body in parts if needle in _squeeze(body)), None)
            if hit:
                flag["source"] = hit
                break
    return flags


# ── 팝업용 요약 (2026-10-02 사용자 요청: 원문 표기가 공고마다 제각각이라 핵심 낱말로 같은 모양을 만든다) ──
# 결과는 [[항목, 값], …] — 화면은 이걸 표로 먼저 보여주고 원문은 아래에 작게. 못 뽑은 항목은 빼고, 아무것도 못 뽑으면 [].
_AMOUNT_RE = re.compile(
    r"(\d[\d,.]*\s*(?:억|천만|백만)\s*(?:\d[\d,.]*\s*(?:천만|백만|만)\s*)?원?|\d[\d,.]*\s*만?\s*원)\s*(?:\([^)]{0,15}\)\s*)?(이상|초과)?"
)
# 실적 대상 분야는 괄호 안에 "…관련/…분야"로 적는 경우가 많다 — 발주기관 나열("국가 및 지자체 등")은 대상이 아니다
_FIELD_PAREN_RE = re.compile(r"\(([^()]{4,60}(?:관련|분야)[^()]{0,20})\)")
_VAT_RE = re.compile(r"VAT\s*포함|부가\s*가치\s*세\s*포함|부가세\s*포함")
_VAT_EXCL_RE = re.compile(r"부가\s*(?:가치\s*)?세\s*별도|VAT\s*별도")
_PERIOD_RE = re.compile(r"최근\s*(\d+)\s*개?\s*년|(\d+)\s*개?\s*년\s*(?:이내|간|동안|내)")
# "최근 3년(완료일자 기준)"처럼 기간 바로 뒤 괄호의 기준
_PERIOD_BASIS_RE = re.compile(r"\s*\(([^)]{0,12}기준)\)")
# "2021년 이후/부터"처럼 시작 연도로 적은 기간
_SINCE_YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*(?:년|\.)\s*(?:\d{1,2}\s*(?:월|\.)\s*(?:\d{1,2}\s*일?\.?)?\s*)?(?:이후|부터)")
# 실적 대상 앞쪽 경계 — 기간·발주처·금액 표현 뒤부터가 "무엇을 했는지"다
_TARGET_START_RE = re.compile(
    r".*(?:이상|원|발주한|시행한|발주하거나|투자한|의한|따른|이내에?|년간|기준|으로|당)\s*(?=\S)"
)
_TARGET_TRIM_HEAD = re.compile(r"^(?:[의에을를로]\s+|규모의\s*|단일\s*(?:사업|건)?\s*(?:으로|당)?\s*)")
_TARGET_TRIM_TAIL = re.compile(r"\s*(?:준공된|된|한|하는|을|를|의|이|가|으로|로|사업으로|관련|등의|단일\s*(?:사업|건)?\s*(?:으로)?|수행)\s*$")


def _sq(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _won(amt: str) -> str:
    """금액 표기를 "N억원"/"N천만원"으로 맞춘다 — "100,000,000원"·"50백만원"·"334백만원"·"1억"."""
    m = re.fullmatch(r"([\d,.]+)(억|천만|백만|만)?원?", amt)
    if not m:
        return amt if amt.endswith("원") else amt + "원"
    try:
        n = float(m.group(1).replace(",", "")) * {"억": 1e8, "천만": 1e7, "백만": 1e6, "만": 1e4, None: 1}[m.group(2)]
    except ValueError:
        return amt
    if n >= 1e8:
        return f"{n / 1e8:g}억원"
    if n >= 1e7 and n % 1e7 == 0:
        return f"{n / 1e7:g}천만원"
    if n >= 1e4 and n % 1e4 == 0:
        return f"{n / 1e4:,.0f}만원"
    return amt


def _target(line: str) -> str | None:
    paren = _FIELD_PAREN_RE.search(line)
    if paren:
        return _sq(paren.group(1))
    pre = _sq(re.sub(r"[(\[][^)\]]*[)\]]", " ", line.split("실적")[0]))
    pre = re.sub(r"^\s*(?:[-·•◦○❍※]|\d{1,2}\)|[①-⑳])\s*", "", pre)
    m = _TARGET_START_RE.match(pre)
    seg = pre[m.end():] if m else pre
    for _ in range(3):
        seg = _TARGET_TRIM_TAIL.sub("", _TARGET_TRIM_HEAD.sub("", seg)).strip(" ,·․’'\"“”")
    return seg if 3 <= len(seg) <= 50 else None


def _track_summary(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    # 기간·금액은 늘 보이게(10/2 요청) — 원문에서 못 찾으면 "원문에서 못 찾음"으로 적는다(원문은 팝업 아래에 그대로)
    period = _PERIOD_RE.search(text)
    since = _SINCE_YEAR_RE.search(text)
    if period:
        basis = _PERIOD_BASIS_RE.match(text, period.end())
        rows.append(["기간", f"최근 {period.group(1) or period.group(2)}년" + (f" ({_sq(basis.group(1))})" if basis else "")])
    elif since:
        rows.append(["기간", f"{since.group(1)}년 이후"])
    else:
        rows.append(["기간", "원문에서 못 찾음"])
    amounts, targets = [], []
    for line in text.split("\n"):
        for m in _AMOUNT_RE.finditer(line):
            if re.fullmatch(r"\d+", m.group(1).strip()):
                continue
            kind = "단일" if re.search(r"단일|1\s*건", line) else "누적" if re.search(r"누적|합산|합계", line) else ""
            amounts.append(" ".join(x for x in (kind, _won(re.sub(r"\s+", "", m.group(1))), m.group(2) or "이상") if x))
        if "실적" in line:
            t = _target(line)
            if t:
                targets.append(t)
    rows.append(["금액", " / ".join(dict.fromkeys(amounts)) if amounts else "원문에서 못 찾음"])
    if targets:
        rows.append(["대상", " / ".join(dict.fromkeys(targets))])
    if _VAT_RE.search(text):
        rows.append(["비고", "부가세 포함 금액"])
    elif _VAT_EXCL_RE.search(text):
        rows.append(["비고", "부가세 별도 금액"])
    return rows


def _staff_summary(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    role = re.search(r"([가-힣·\s및]{2,24}?)(?:을|를)\s*담당하는\s*자", text)
    if role:
        rows.append(["역할", _sq(role.group(1))])
    quals = list(dict.fromkeys(m.group(0) for m in re.finditer(r"[가-힣]{0,8}(?:기술사|건축사|기술자|학예사|기사)(?:\s*자격(?:증)?)?", text)))
    if quals:
        rows.append(["자격", " / ".join(_sq(q) for q in quals[:3])])
    count = re.search(r"(\d+)\s*(?:인|명)\s*이상", text)
    if count:
        rows.append(["인원", f"{count.group(1)}명 이상"])
    if re.search(r"콘소시엄|컨소시엄|계약서", text):
        rows.append(["보완", "컨소시엄·계약으로 확보 가능(증빙 첨부)"])
    return rows


def _office_summary(text: str) -> list[list[str]]:
    rows = [["요건", "건축사사무소 개설 신고(등록)"]]
    law = re.search(r"건축사법[」\"']?\s*(제\s*\d+\s*조)?", text)
    if law:
        rows.append(["근거", _sq("건축사법 " + (law.group(1) or "")).strip()])
    return rows


def summarize_flag(flag: dict) -> list[list[str]]:
    text = flag.get("text", "")
    kind = flag.get("kind")
    if kind == "실적":
        return _track_summary(text)
    if kind == "인력":
        return _staff_summary(text)
    if kind == "건축사사무소":
        return _office_summary(text)
    return []
