"""자격조건(면허·업종 제한) 판정.

판정 틀은 기존 시스템(`matching/qualificationFilter.ts`)을 따른다. 단, 업종 이름 비교는
기존 코드의 양방향 부분일치 대신 **코드 비교 + 이름 완전일치**로 바꿨다(2026-09-30 사용자 결정 —
기존 코드에 부분일치를 쓰라는 규칙 설명이 없었고, "실내건축공사업"이 "건축공사업"을 충족하는 오판을 냈다).

  면허제한정보 API의 구조 (2026-09-30 실측 두 건으로 바로잡음 — 예전엔 그룹끼리 "모두 필요"로 봤다):
  - 제한그룹(`lmtGrpNo`)끼리는 **"또는"** — 그룹 하나만 다 채우면 참가 가능.
    나라장터 화면 "[출판사(1517)] 업종 또는 [인쇄사(1518)] 업종을 등록한 업체"가 API에선 그룹1 출판사,
    그룹2 인쇄사로 온다(국립세종도서관 정책도서 발행). 백령 체험관 "건축(또는 토목건축)공사업"도 같은 모양.
  - 한 그룹 안의 행(`lmtSno`)끼리는 **"모두 필요"**, 행 하나의 허용업종목록(`permsnIndstrytyList`)은
    그 행을 대신할 수 있는 업종("또는")이다.
  - 화면·리포트는 "요건마다 이 중 하나"(AND of OR) 모양으로 보여주므로 `license_requirements`가
    그룹들을 그 모양으로 펼친다. 조회 실패 / 정보 없음이면 걸러내지 않고 통과 (fail-open).

공동수급·지역은 **판정에 개입하지 않고 정보로만** 수집한다.

API 면허제한정보가 비어 있는 공고는 `evaluate_attachment_text`가 첨부파일
참가자격 절에서 뽑은 업종코드·세부품명번호로 같은 형태의 판정을 만든다
(`attachments.save_attachment_texts`가 호출). 이 판정은 표시만 갱신하고
후보 목록 자체(포함/제외)는 바꾸지 않는다 — 이미 API 기반 1차 판정이 끝난
뒤에 붙는 보조 재판정이기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import fields as F
from .http_client import ApiError, DataGoKrClient, RawItem

# 기존 시스템과 동일. 충족 못한 그룹이 이 개수까지는 통과시킨다.
MAX_ALLOWED_MISSING_QUALIFICATIONS = 1


@dataclass
class LicenseGroup:
    group_no: str
    allowed_names: list[str] = field(default_factory=list)
    # 면허제한 API 그룹일 때만: 행(lmtSno)별 허용 이름 목록. 행끼리는 모두 필요, 행 안은 "또는".
    # 비어 있으면 allowed_names 전체가 한 행("이 중 하나")이다.
    rows: list[list[str]] = field(default_factory=list)
    # "조합 중 하나" 요건일 때만: 조합(=API 제한그룹) 목록. 조합 하나 = 행 목록(모두 필요), 행 = 대신 인정되는
    # 이름 목록("또는"). 나라장터 "[A]과 [B] 업종 또는 [C]과 [D] 업종"을 원문처럼 조합으로 보여주려고 둔다.
    combos: list[list[list[str]]] = field(default_factory=list)


@dataclass
class QualificationResult:
    total_groups: int
    missing_groups: list[LicenseGroup]
    passes: bool
    checked: bool  # False면 자격정보가 없어 판정을 못 한 것 (fail-open으로 통과)
    # 충족된 그룹. HTML 리포트의 파란 체크원(자격 충족) 호버 팝업에 미보유 쪽과
    # 같은 형식("이름(코드)")으로 보여주기 위한 것 — 판정 자체(passes/missing_groups)엔
    # 관여하지 않는다. 직접 QualificationResult(...)를 만드는 기존 테스트 코드가
    # 전부 깨지지 않도록 기본값을 빈 리스트로 둔다.
    satisfied_groups: list[LicenseGroup] = field(default_factory=list)
    # 첨부파일 판정에서 이름을 끝내 못 찾은 미보유 코드. 로그로 알려서
    # config/code_names.json에 추가하게 한다 (판정 자체와는 무관).
    unnamed_codes: list[str] = field(default_factory=list)

    @property
    def missing_count(self) -> int:
        return len(self.missing_groups)

    @property
    def summary(self) -> str:
        """지일이 요구 자격을 다 가지고 있으면 '자격 충족', 아니면 미보유 자격증 이름을 붙여
        '자격 미달(이름)'로 표시한다 — 담당자가 그룹/카운트 계산 없이 바로 알아볼 수 있게."""
        if not self.checked:
            return "자격정보 미확인 (통과)"
        if self.missing_count == 0:
            return "자격 충족"
        # 실측(2026-09-17 Run #45, R26BK01731335): 첨부문서 한 항목 안에서도
        # 같은 코드가 중복 추출될 수 있어(예: "세부품명번호 10자리, ####"와
        # 괄호 표기가 같은 줄에 같이 있는 경우) 그룹 하나의 allowed_names
        # 자체가 ["A", "A"]가 된다 — 이걸 그대로 "/"로 이으면 "A/A"가 되고,
        # 다른 그룹의 단순 "A"와 문자열이 달라져서 아래 그룹 간 중복 제거를
        # 통과해버린다("자격 미달(A/A, A)"). 그룹 안에서 먼저 이름 중복을
        # 제거한 뒤 이어야, 같은 요건이면 다른 그룹이라도 "A"로 합쳐진다.
        # 같은 자격이 여러 그룹에서 각각 미충족으로 걸리는 경우(예: 첨부문서
        # 항목 여러 개가 같은 코드를 요구)도 이 순서로 함께 걸러진다.
        # "반드시" 요건(rows 한 줄)은 대표 면허만 — 나라장터가 대신 인정하는 업종까지 늘어놓으면 요약이 읽히지 않는다
        names = (
            g.rows[0][0] if len(g.rows) == 1 and g.rows[0] else "/".join(dict.fromkeys(g.allowed_names))
            for g in self.missing_groups
        )
        # 같은 코드인데 표기만 다른 요건("G2B분류번호 교육훈련장비(6010999901)"·"육훈련장비(6010999901)")은 한 번만
        by_code: dict[str, str] = {}
        for name in names:
            code = re.search(r"\((\d{4,10})\)$", name)
            by_code.setdefault(code.group(1) if code else name, name)
        missing_names = ", ".join(by_code.values())
        return f"자격 미달({missing_names})"


def split_industry_list(text: str) -> list[str]:
    """허용업종목록 텍스트를 개별 업종명으로 나눈다.

    실측 형태는 "[업종명/업종코드][업종명/업종코드]…" 이다 (예: "[실내건축공사업/4990]").
    대괄호 단위로 나눈 뒤 마지막 '/' 뒤의 코드를 떼어 업종명만 남긴다.
    대괄호가 없으면 콤마·슬래시로 나눈다.

    허용업종 목록의 코드는 여기서 버린다(이름 완전일치용). 코드 비교는 같은 그룹의
    lcnsLmtNm("건축공사업/0002")에 남은 코드로 한다. 표시용 "이름(코드)" 변환은
    `report.py`에서 별도로 한다.
    """
    bracketed = re.findall(r"\[([^\]]+)\]", text)
    if bracketed:
        out = []
        for entry in bracketed:
            name = entry.rsplit("/", 1)[0] if "/" in entry else entry
            name = name.strip()
            if name:
                out.append(name)
        return out

    return [s.strip() for s in re.split(r"[,/·、]", text) if s.strip()]


def group_license_rows(raw_items: list[RawItem]) -> dict[str, list[LicenseGroup]]:
    """면허제한 응답을 공고번호별 제한그룹 목록으로 정리한다.

    주의: 이 오퍼레이션은 bidNtceNo를 넘겨도 서버가 필터링하지 않고 조회기간 전체를
    페이지 단위로 내려준다. 그래서 공고별로 호출하지 않고 **기간 전체를 1회 받아
    여기서 공고번호별로 묶는다.** 공고 수가 늘어도 API 호출 횟수가 늘지 않는다.
    """
    # 공고번호 → 차수 → 그룹 → 행. 정정공고로 차수가 여럿이면 조회기간 안에 차수마다 같은 행이 또 온다 —
    # 전부 한 공고로 합치면 같은 면허가 차수 수만큼 겹쳐 나온다(2026-09-30 제보: 토목공사업이 3번,
    # 상·하수도/지반조성이 번갈아 3번). **가장 마지막 차수만** 쓴다(정정 내용이 최신이다).
    by_notice: dict[str, dict[str, dict[str, list[list[str]]]]] = {}

    for item in raw_items:
        notice_no = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "notice_no")
        group_no = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "group_no")
        if not notice_no or not group_no:
            continue
        notice_ord = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "notice_ord") or ""

        names: list[str] = []
        license_name = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "license_name")
        if license_name:
            # 실측: lcnsLmtNm 필드 자체가 "실내건축공사업/4990"처럼 코드를
            # 슬래시로 붙여 내려온다. split_industry_list와 마찬가지로 매칭용
            # allowed_names에선 코드를 버리지 않고 원문 그대로 둔다(양방향
            # 부분일치가 깨지지 않는 값 그대로) — "이름(코드)" 표시 변환은
            # report.py에서 별도로 한다.
            names.append(license_name)
        allowed = F.pick_by(item, F.LICENSE_LIMIT_FIELDS, "allowed_industries")
        if allowed:
            names.extend(split_industry_list(allowed))
        if not names:
            continue

        rows = by_notice.setdefault(notice_no, {}).setdefault(notice_ord, {}).setdefault(group_no, [])
        row = _dedupe_names(names)
        if not row:
            continue
        # 같은 그룹에서 대표 면허(lcnsLmtNm)가 같은 행은 **한 요건**이다 — 허용업종만 다른 행이 여러 개 오거나
        # 똑같은 행이 되풀이돼도(2026-09-30 제보: 토목공사업 ×3, 상·하수도/지반조성 번갈아 ×3) 대표 면허 하나로
        # 합치고 허용업종은 "또는"으로 모은다. 행끼리는 "모두 필요"라 같은 면허를 두 번 세면 안 된다.
        same = next((r for r in rows if _license_base(r[0]) == _license_base(row[0])), None)
        if same is None:
            rows.append(row)
        else:
            same[:] = _dedupe_names(same + row)

    out: dict[str, list[LicenseGroup]] = {}
    for notice_no, by_ord in by_notice.items():
        groups = by_ord[max(by_ord, key=lambda o: (len(o), o))]  # "000" < "001" < … (자릿수 같음)
        out[notice_no] = [
            LicenseGroup(group_no=g, allowed_names=_dedupe_names([n for row in rows for n in row]), rows=rows)
            for g, rows in groups.items()
        ]
    return out


def _is_group_satisfied(group: LicenseGroup, held_names: list[str], held_codes: set[str] | frozenset[str] = frozenset()) -> bool:
    """그룹 안 허용업종 중 하나라도 보유하면 충족(OR).

    - 허용업종에 코드가 붙어 있으면("건축공사업/0002", "건축공사업(0002)") 보유 코드와 비교한다.
    - 그리고 코드를 뗀 이름이 보유 업종 이름과 **완전히 같으면**(공백 무시) 충족이다.
      부분일치는 쓰지 않는다 — "실내건축공사업"은 "건축공사업"이 아니다.

    예전엔 이름 양방향 부분일치였다 — 옮겨 온 기존 코드(`qualificationFilter.ts`)가 그렇게 짜여 있었을 뿐
    규칙으로 정해진 근거는 없었다. 그 탓에 보유 "실내건축공사업(0006)"이 "건축공사업(0002)"을 충족한
    것으로 나왔다(2026-09-30, 옹진군 백령 체험관 증축공사). 사용자 결정으로 코드 비교로 바꿈.
    """
    if group.combos:
        return any(
            all(_is_group_satisfied(LicenseGroup("", row), held_names, held_codes) for row in combo)
            for combo in group.combos
        )
    held_bases = {_license_base(h) for h in held_names}
    for allowed in group.allowed_names:
        code = _license_code(allowed)
        if (code and code in held_codes) or _license_base(allowed) in held_bases:
            return True
    return False


def no_held_license(
    groups: list[LicenseGroup], held_names: list[str], held_codes: set[str] | frozenset[str] = frozenset()
) -> list[str] | None:
    """공고가 요구하는 면허를 지일이 **하나도** 갖고 있지 않으면 요구 면허 이름 목록, 아니면 None.

    공동수급(분담이행)은 구성원이 맡는 부분의 면허를 갖고 있어야 해서, 요구 면허 중 지일 것이 하나도 없으면
    공동수급으로도 들어갈 자리가 없다(2026-10-13 사용자 결정 — 조경·토목공사업만 요구하는 공고도 여기에 해당).
    어느 그룹·어느 행이든 지일 보유 면허(대표 면허나 허용업종)가 하나라도 있으면 None(남겨서 자격 판정·공동수급 칸으로 판단).
    면허제한 정보가 없으면 None.
    """
    if not groups:
        return None
    names: list[str] = []
    for group in groups:
        rows = [r for r in (group.rows or [group.allowed_names]) if r]
        for row in rows:
            if _is_group_satisfied(LicenseGroup("", row), held_names, held_codes):
                return None
            label = _row_label(row)
            if label not in names:
                names.append(label)
    return names or None


_LICENSE_CODE_RE = re.compile(r"(?:/\s*|\()(\d{4,10})\)?\s*$")


def _license_code(name: str) -> str | None:
    """'토목건축공사업/0003', '토목건축공사업(0003)' → '0003'. 코드가 없으면 None."""
    m = _LICENSE_CODE_RE.search(name)
    return m.group(1) if m else None


def _license_base(name: str) -> str:
    """'토목건축공사업/0003', '토목건축공사업(0003)', '토목건축공사업' → '토목건축공사업' (비교용)."""
    name = re.sub(r"\s*/\s*\d{4,10}\s*$", "", name)
    name = re.sub(r"\(\d{4,10}\)\s*$", "", name)
    return re.sub(r"\s+", "", name)


def _dedupe_names(names: list[str]) -> list[str]:
    """같은 면허가 "토목건축공사업"·"토목건축공사업/0003"처럼 두 번 들어오면 코드 있는 쪽 하나만 남긴다."""
    out: dict[str, str] = {}
    for n in names:
        base = _license_base(n)
        if not base:
            continue
        if base not in out or (_license_code(n) and not _license_code(out[base])):
            out[base] = n
    return list(out.values())


def _row_label(row: list[str]) -> str:
    """행의 대표 이름(lcnsLmtNm, 맨 앞)을 "이름(코드)"로."""
    name = row[0]
    code = _license_code(name)
    return f"{_license_base(name)}({code})" if code else _license_base(name)


def license_requirements(
    groups: list[LicenseGroup], held_names: list[str] = (), held_codes: set[str] | frozenset[str] = frozenset()
) -> list[LicenseGroup]:
    """면허제한 그룹들("그룹 A 또는 그룹 B", 그룹 안 행은 모두 필요)을 화면·판정용 요건 목록으로 바꾼다.
    요건끼리는 모두 필요하다. 공고문이 쓰는 모양("① 반드시 + ②~⑦ 중 1개 이상")에 맞춘다.

    - 그룹이 하나 → 그 그룹의 행이 각각 "반드시" 요건
    - 모든 그룹에 똑같이 든 행(대표 면허 기준) → "반드시" 요건 (폐기물 공고의 수집·운반업 1227)
    - 나머지가 그룹마다 한 행씩 → 그 행들을 합친 "이 중 하나" 요건 1건 (출판사 또는 인쇄사 / ②~⑦ 중 1개)
    - 나머지가 그보다 복잡하면 → "아래 조합 중 하나" 요건 1건 (`combos`, "A와 B" 또는 "C와 D")
    "반드시" 요건은 `rows=[행]`을 달아 둔다 — 행의 맨 앞이 대표 면허, 나머지는 나라장터가 대신 인정하는 업종.
    """
    grouped = [[_dedupe_names(r) for r in (g.rows or [g.allowed_names]) if _dedupe_names(r)] for g in groups]
    grouped = [rows for rows in grouped if rows]
    if not grouped:
        return []

    def key(row: list[str]) -> str:
        return _license_base(row[0])

    def must(no: str, row: list[str]) -> LicenseGroup:
        return LicenseGroup(group_no=no, allowed_names=list(row), rows=[list(row)])

    if len(grouped) == 1:
        return [must(f"필수{i + 1}", r) for i, r in enumerate(grouped[0])]

    common_keys = set.intersection(*({key(r) for r in rows} for rows in grouped))
    out = [must(f"필수{i + 1}", r) for i, r in enumerate(r for r in grouped[0] if key(r) in common_keys)]
    residuals = [[r for r in rows if key(r) not in common_keys] for rows in grouped]
    if any(not res for res in residuals):  # 공통 면허만으로 채워지는 그룹이 있으면 나머지는 선택 사항
        return out
    if all(len(res) == 1 for res in residuals):
        out.append(LicenseGroup(group_no="택1", allowed_names=_dedupe_names([n for res in residuals for n in res[0]])))
        return out
    slots = _factor_slots(residuals, key)
    if slots:
        # 조합이 "(A 또는 B) 그리고 (C 또는 D)"로 풀리면 요건 여러 건으로 — 경우의 수를 다 나열하지 않는다(2026-10-01 요청)
        for i, slot in enumerate(slots):
            if len(slot) == 1:
                out.append(must(f"필수{len(out) + 1}", slot[0]))
            else:
                out.append(LicenseGroup(group_no=f"택1-{i + 1}", allowed_names=_dedupe_names([n for r in slot for n in r])))
        return out
    labels = [" + ".join(_row_label(r) for r in res) for res in residuals]
    out.append(LicenseGroup(group_no="조합", allowed_names=list(dict.fromkeys(labels)), combos=residuals))
    return out


def _factor_slots(residuals: list[list[list[str]]], key) -> list[list[list[str]]] | None:
    """그룹들이 "칸마다 하나씩 고른 모든 조합"인지 본다 — 예: {A,C} {A,D} {B,C} {B,D} = (A 또는 B) 그리고 (C 또는 D).
    맞으면 칸 목록(칸마다 행 목록)을, 아니면 None. 한 그룹 안에 같이 나온 면허는 다른 칸, 한 번도 같이 안 나온 면허는 같은 칸.
    """
    rows_by_key: dict[str, list[str]] = {}
    sets = []
    for res in residuals:
        keys = frozenset(key(r) for r in res)
        if len(keys) != len(res):
            return None
        for r in res:
            rows_by_key.setdefault(key(r), r)
        sets.append(keys)
    unique = set(sets)
    if len({len(s) for s in unique}) != 1:
        return None
    together = {k: set() for k in rows_by_key}
    for s in unique:
        for k in s:
            together[k] |= s - {k}
    slots: list[list[str]] = []
    for k in rows_by_key:  # 처음 나온 순서대로
        for slot in slots:
            if not any(k in together[other] for other in slot):
                slot.append(k)
                break
        else:
            slots.append([k])
    if len(slots) < 2 or any(sum(1 for k in s if k in slot) != 1 for s in unique for slot in slots):
        return None
    total = 1
    for slot in slots:
        total *= len(slot)
    if total != len(unique):  # 모든 조합이 다 있어야 칸으로 나눌 수 있다
        return None
    return [[rows_by_key[k] for k in slot] for slot in slots]


def evaluate(
    groups: list[LicenseGroup], held_names: list[str], held_codes: set[str] | frozenset[str] = frozenset()
) -> QualificationResult:
    if not groups:
        return QualificationResult(total_groups=0, missing_groups=[], passes=True, checked=False)
    groups = license_requirements(groups, held_names, held_codes)

    missing = [g for g in groups if not _is_group_satisfied(g, held_names, held_codes)]
    satisfied = [g for g in groups if g not in missing]
    return QualificationResult(
        total_groups=len(groups),
        missing_groups=missing,
        passes=len(missing) <= MAX_ALLOWED_MISSING_QUALIFICATIONS,
        checked=True,
        satisfied_groups=satisfied,
    )


# "(업종코드 4990)" / "(세부품명번호 6010989901)" / "(디지털콘텐츠개발서비스사업,
# 업종코드 1469)" — 참가자격 문서가 실측상 이 표기로 업종·품목을 명시한다.
# held_qualifications.json의 code와 그대로 비교할 수 있다. 실측: 일부 문서는
# "세부품명번호 10자리, 4924159701"처럼 자릿수 설명을 코드 앞에 끼워 넣는다 —
# 그 필러를 건너뛰지 않으면 "10"을 코드로 잘못 잡는다. 업종코드 4자리,
# 세부품명번호 10자리라 4~10자리로 캡처를 제한해 이런 오탐도 같이 막는다.
#
# 두 번째 대안은 키워드 없이 "이름(코드10자리)"만 쓰는 문서용이다(실측: 두바이
# 의료기기전시회 한국관 공고문 — "전시부스설치서비스(7215409901)"). 정확히
# 10자리로 제한해 일반 괄호 안 숫자(연도·조항 번호 등)를 코드로 오인하지
# 않게 한다.
_CODE_REQUIREMENT_RE = re.compile(
    # 실측 표기 중 "세부품명번호: 7215409901"처럼 콜론이 끼는 경우도 받는다.
    r"(?:업종코드|세부품명번호)\s*[:：]?\s*(?:[0-9]+\s*자리\s*,?\s*)?(?P<code>[0-9]{4,10})"
    r"|\((?P<bare_code>[0-9]{10})\)"
)
# 문서마다 괄호를 쓰는지 대괄호를 쓰는지, 코드 여러 개를 콤마로 나열하는지
# 세미콜론이나 공백으로 나열하는지가 제각각이다 — 문서마다 정규식을 하나씩
# 추가하는 대신, "괄호/대괄호 하나 안에 코드가 몇 개 있든, 구분자가 뭐든
# 전부 뽑는다"로 일반화한다. "세부품명번호 10자리(코드1 이름1, 코드2 이름2)"처럼
# 프리픽스 바로 뒤에 괄호가 오면(키워드로 확신 가능) 4~10자리를 코드로 보고,
# "[코드, 이름]"처럼 키워드 없이 괄호/대괄호만 쓰면(실측: 직접생산확인증명서
# 항목) 세부품명번호 자릿수(정확히 10자리)일 때만 코드로 인정한다 — 키워드가
# 없어 짧은 숫자는 법조문·연도 인용과 구분할 수 없기 때문이다.
_PREFIXED_GROUP_RE = re.compile(
    r"(?:업종코드|세부품명번호)\s*[:：]?\s*(?:[0-9]+\s*자리\s*,?\s*)?[(\[]([^()\[\]]*)[)\]]"
)
_BARE_GROUP_RE = re.compile(r"[(\[]([^()\[\]]*)[)\]]")
# 키워드가 괄호 **안쪽 맨 앞**에 오는 표기 — 실측(사용자 제보, 2026-09-17):
# "산업디자인 전문업[업종코드 4440, 4442, 4444]". 이걸 따로 안 잡으면 아래
# _CODE_REQUIREMENT_RE가 첫 코드(4440)만 잡고, 이름표도 괄호 앞 문장 조각
# ("산업디자인 전문업[")으로 만들어 리포트에 이상한 이름이 떴다.
_INNER_PREFIXED_GROUP_RE = re.compile(
    r"[(\[]\s*(?:업종코드|세부품명번호)\s*(?:[0-9]+\s*자리\s*,?\s*)?([^()\[\]]*)[)\]]"
)
_GROUP_ENTRY_CODE_RE = re.compile(r"[0-9]{4,10}")
_INNER_ITEM_NAME_RE = re.compile(r"(?:세부)?품명\s*[:：]\s*([^,，;()\[\]]+?)\s*(?:[,，;]|$)")
_BARE_GROUP_ENTRY_CODE_RE = re.compile(r"[0-9]{10}")
_GROUP_ENTRY_STRIP_CHARS = " ,·/;、"
# "다음 중 어느 하나"는 항목 전체를 OR로 만든다.
_OR_MARKER_RE = re.compile(r"어느\s*하나")
_CODE_HINT_RE = re.compile(r"업종\s*코드|세부\s*품명\s*번호|분류\s*번호|(?<!\d)\d{10}(?!\d)")
# 코드 바로 뒤(닫는 괄호 다음)에 "또는/혹은"이 오면 그 앞뒤 두 코드만 OR로 묶는다
# (실측: 2026 대한민국 지방시대 엑스포 R26BK01739064 — "전시부스설치및디자인서비스
# (세부품명번호 : 7215409901) 또는 전시홍보관설치및디자인서비스(세부품명번호 :
# 7215409902)"). 항목 전체를 OR로 만들면 "다음을 모두 소지: (A 또는 B), C"에서
# C 미보유를 놓친다 — 그래서 "또는"으로 이어진 코드끼리만 묶고 묶음 사이는 AND다.
# "A, B 또는 C"처럼 쉼표 나열 끝에 "또는"이 오면 A·B·C 전체가 하나의 OR 묶음이다.
# "또는"을 항목 어디서나 보면 "공동수급 또는 단독" 같은 무관한 문장에도 걸리므로
# 코드 바로 뒤에 오는 경우로만 좁힌다.
_OR_LINK_RE = re.compile(r"[ \t\u3000]*[)\]]?[ \t\u3000]*,?\s*(?:또는|혹은)")
_COMMA_LINK_RE = re.compile(r"[ \t\u3000]*[)\]]?[ \t\u3000]*,")
# 같은 줄에 요건이 둘 이상 나열되면(PDF 등) 뒷 요건의 이름표 앞에 앞 요건의
# 괄호와 접속어가 딸려온다 — 닫는 괄호 이후만 남기고 앞머리 접속어를 뗀다.
_LEADING_CONJUNCTION_RE = re.compile(r"^(?:또는|혹은|및|그리고)\s+")
_CONJUNCTION_ONLY_RE = re.compile(r"\s*(?:또는|혹은|및|그리고|등|/)?\s*")
# 이름표에서 떼어낼 절차성 어구. 법령 인용("~에 따른/따라/의하여")과, 실측으로
# 확인된 마감 안내 어구("~까지")를 둘 다 다룬다 — 실측(사용자 제보,
# 2026-09-17): "...규정」에 의하여 국가종합전자조달시스템G2B(나라장터)에
# 입찰참가자격등록 마감일시까지 조합놀이대(세부품명번호...)"에서 "마감일시까지"
# 뒤가 실제 품목명이었다. "에 따라"는 "규정에 따라"로 좁혀 놓으면 다른 인용구
# ("「…법」 제9조에 따라" 등)를 놓친다 — 앞 단어를 가리지 않고 일반화한다.
_LABEL_CONNECTOR_RE = re.compile(
    r"(?:에\s*따른|에\s*따라|에\s*의하여|에\s*의한|의한|에\s*의거|에\s*해당하는|까지|포함한|등록한\s*자\s*또는)\s*"
)
_MAX_LABEL_LEN = 20
# "충족된 자격" 팝업에 보여줄 목록을 만들 때 쓴다 — 실측: "[업종코드 4440, 4442,
# 4444]로 등록되어 있는 업체"처럼 키워드가 괄호 **안쪽 맨 앞**에 오고 코드 여러
# 개가 나열되는 표기는 위 단계별 추출(_extract_code_requirements)이 첫 코드만
# 잡고 나머지를 놓친다(뒤 코드들이 이미 소비된 괄호 구간 안에 있어 바깥 규칙이
# 건너뜀). 정확한 이름-코드 매칭·AND/OR 판정은 여전히 위 추출 결과로 하되,
# 화면에 보여줄 "충족" 목록만은 참가자격 절 원문 전체에서 보유 코드(4자리 또는
# 10자리, 앞뒤가 숫자가 아닌 독립된 숫자열)가 실제로 나오는지 직접 훑어서
# 괄호/키워드 위치에 상관없이 다 잡는다. 판정(자격 미달 여부) 자체는 그대로
# _extract_code_requirements의 그룹 단위 AND/OR 로직을 따른다 — 이 스캔은
# 표시용일 뿐이다.
_STANDALONE_CODE_RE = re.compile(r"(?<!\d)(?:\d{10}|\d{4})(?!\d)")


# 이름표 맨 앞의 항목 번호 — "가.", "1)", "(가)", "①" 등. 참가자격 항목은 대개
# 번호로 시작해서, 괄호 앞 구절을 이름표로 쓰면 "나. 직접생산확인증명서"처럼 번호가
# 같이 붙어 나왔다.
_ITEM_MARKER_RE = re.compile(r"^\s*(?:[가-하]\s*[.)]|[0-9]+\s*[.)]|\([가-하0-9]+\)|[①-⑳])\s*")


def _last_name_chunk(text: str) -> str:
    """코드 괄호 바로 앞의 이름 조각. 앞 항목("…(코드1), ")은 버리되, 이름 자체가 괄호로
    끝나면("산업디자인전문회사(환경 디자인분야)[업종코드 4442]") 그 괄호까지 이름으로 남긴다."""
    stripped = text.rstrip()
    if stripped.endswith(")"):
        depth = 0
        for i in range(len(stripped) - 1, -1, -1):
            ch = stripped[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
                if depth == 0:
                    head = re.split(r"[)\]]", stripped[:i])[-1]
                    inner = re.sub(r"\s+", "", stripped[i + 1 : -1])
                    if head.strip() and not re.fullmatch(r"[0-9]{4,10}", inner):
                        return f"{head}({inner})"
                    break
    return re.split(r"[)\]]", text)[-1]


def _truncate_label(name: str) -> str:
    name = _ITEM_MARKER_RE.sub("", name)
    # "산업디자인전문회사(환경디자인을포함한종합디자인분야)"처럼 괄호 분야명이 붙은 이름은 길다 —
    # 20자로 자르면 "회사(…)"만 남아서, 괄호로 끝나는 이름은 괄호 앞 단어까지 살리도록 넉넉히 둔다.
    limit = _MAX_LABEL_LEN
    paren = re.search(r"\(([^()]*)\)$", name)
    if paren:
        limit = _MAX_LABEL_LEN + len(paren.group(0))
    if len(name) > limit:
        name = name[-limit:]
        if " " in name:  # 잘린 앞 단어 조각을 버리고 온전한 단어부터 남긴다
            name = name.split(" ", 1)[1]
    # 잘라낸 자리에 남은 문장부호("직접생산확인증명서 : 영상…" → ": 영상…")
    return re.sub(r"^[\s,;，:：·\-\[]+", "", name)


def _split_group_entries(content: str, code_re: re.Pattern) -> list[tuple[int, str, str]]:
    """괄호/대괄호 안 내용에서 코드-이름 쌍을 뽑는다. 콤마·세미콜론·공백 등
    구분자가 무엇이든 상관없이, 코드 숫자 뒤부터 다음 코드 앞까지를 그
    코드의 이름표로 본다(실측 표기가 "코드 이름, 코드 이름" 순이라 이렇게
    자르면 이름이 온전히 남는다)."""
    matches = list(code_re.finditer(content))
    pairs = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        name = content[match.end() : end].strip(_GROUP_ENTRY_STRIP_CHARS)
        # "(업종코드 4442 또는 4444)"처럼 코드 사이에 접속사만 있으면 그건 이름이 아니다 —
        # 예전엔 "또는(4442)"로 표시됐다. 비워 두면 이름 사전·등록증에서 찾아 채운다.
        if _CONJUNCTION_ONLY_RE.fullmatch(name):
            name = ""
        pairs.append((match.start(), match.group(0), name))
    return pairs


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and a[1] > b[0]


def _merge_wrapped_parens(text: str) -> str:
    """PDF는 페이지 폭 기준으로만 줄바꿈해서, 문장 구조와 무관하게 단어나
    괄호 중간에서 줄이 끊긴다(실측: 한국항공우주연구원 나로우주센터 공고
    R26BK01710751 — "직접생산확인증명서(영상정보디스플레이\\n장치, 4511189301)"
    처럼 "영상정보디스플레이장치"라는 한 단어가 줄바꿈에 걸려 반으로 잘렸다).
    아래 코드 추출은 괄호/대괄호 하나가 한 줄 안에 있다고 보고 그 줄만 본다 —
    그래서 이렇게 걸리면 코드 자체를 통째로 놓친다. 괄호/대괄호가 열린 채로
    줄이 끝나면 그 줄바꿈만 지워 다시 한 줄로 합친다(공백을 넣지 않는다 —
    단어 중간이 끊긴 경우가 많아 공백을 넣으면 오히려 단어가 갈라진다)."""
    out = []
    depth = 0
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "\n" and depth > 0:
            continue
        out.append(ch)
    return "".join(out)


def _extract_code_requirements(item: str) -> list[tuple[str, str]]:
    """항목 한 줄에서 코드 표기가 붙은 요건을 [(코드, "이름(코드)" 이름표)]로 뽑는다.

    이름표는 두 가지 표기 관행을 다룬다 — "이름(업종코드 ####)"(괄호 앞이 이름)와
    "(설명, 업종코드 ####)"(괄호 안 콤마 앞이 이름). 전자는 괄호 앞 구절에서
    `_LABEL_CONNECTOR_RE`가 아는 연결어/절차 어구("…에 따른/따라/의하여", "…까지")를
    떼고 남은 마지막 구절을 이름표로 쓴다. 실측 문서 문장이 다양해 완벽히 못
    떼어낼 수 있으니, 그래도 너무 길면(`_MAX_LABEL_LEN`) 뒷부분만 잘라 쓴다 —
    문장 전체가 그대로 리포트에 나오는 것보다는, 한글 문장 특성상 대상
    명사가 대개 끝에 오므로 뒷부분만 잘라도 알아볼 수 있는 경우가 많다.

    **"마지막 한 단어만 남긴다"처럼 더 단순화하지 말 것** — 이름이 "실물모형
    및 전시물"처럼 여러 단어로 된 경우가 실제로 있어(단어 하나만 남기면
    "전시물"로 정보가 없어진다), 알려진 연결어/절차 어구만 골라서 떼어내는
    지금 방식이 필요하다.

    세 단계로 나눠 뽑고, 뒤 단계는 앞 단계가 이미 잡은 구간을 다시 건드리지
    않는다(겹치면 건너뜀) — 문서마다 괄호/대괄호·구분자 표기가 제각각이라
    형태별로 정규식을 계속 추가하는 대신, 우선순위가 있는 일반 규칙으로
    처리한다.
      1. `_PREFIXED_GROUP_RE`: 프리픽스 바로 뒤 괄호/대괄호 — 키워드로 코드임을
         확신할 수 있어 하나든 여러 개든, 구분자가 뭐든 전부 뽑는다.
      2. `_CODE_REQUIREMENT_RE`: 프리픽스 뒤 코드 하나(괄호 없이), 또는 정확히
         10자리 숫자만 있는 단독 괄호 — 기존 형태, 이름표 추출 로직도 그대로.
      3. `_BARE_GROUP_RE`: 위 두 규칙이 건드리지 않은 나머지 모든 괄호/대괄호 —
         키워드가 없어 확신할 수 없으므로 정확히 10자리(세부품명번호 자릿수)인
         경우만 코드로 인정한다.
    """
    _, located = _locate_code_requirements(item)
    return [(code, label) for _, _, code, label in located]


def _locate_code_requirements(item: str) -> tuple[str, list[tuple[int, int, str, str]]]:
    """`_extract_code_requirements`와 같은 추출을 하되, 괄호 줄바꿈을 합친 원문과
    각 코드의 원문상 위치(시작, 끝)를 같이 돌려준다 — "또는"으로 이어진 코드끼리
    묶을 때(`_or_groups`) 코드 사이 글자를 봐야 해서다. 목록 순서는 추출 단계
    순서(`_extract_code_requirements`와 동일)이고 위치순이 아니다."""
    merged = _merge_wrapped_parens(item)
    results: list[tuple[int, int, str, str]] = []
    offset = 0
    for line in merged.split("\n"):
        consumed: list[tuple[int, int]] = []
        base = offset
        offset += len(line) + 1

        # 0. 괄호 안쪽 맨 앞 키워드 + 코드 여러 개 — 코드 뒤에 이름이 없으면 이름표를
        #    코드만으로 둔다(evaluate_attachment_text가 사전 등에서 이름을 찾아 채운다).
        #    코드가 하나뿐이면 건너뛴다: "실내건축공사업(업종코드 4990)"은 괄호 앞이
        #    이름이라 아래 2단계가 이름표를 더 잘 만든다.
        for group_match in _INNER_PREFIXED_GROUP_RE.finditer(line):
            entries = _split_group_entries(group_match.group(1), _GROUP_ENTRY_CODE_RE)
            if len(entries) < 2:
                continue
            consumed.append(group_match.span())
            for pos, code, name in entries:
                name = _truncate_label(name)
                start = base + group_match.start(1) + pos
                results.append((start, start + len(code), code, f"{name}({code})" if name else code))

        for group_match in _PREFIXED_GROUP_RE.finditer(line):
            if any(_spans_overlap(group_match.span(), span) for span in consumed):
                continue
            consumed.append(group_match.span())
            entries = _split_group_entries(group_match.group(1), _GROUP_ENTRY_CODE_RE)
            # "[세부품명: 실물모형및전시물, 세부품명번호 10자리(6010989901)" — 코드 하나에 이름이 없으면
            # 바로 앞에 따로 적힌 "품명: …"을 이름으로 쓴다(실측: 울산박물관 R26BK01748232).
            lead = _INNER_ITEM_NAME_RE.search(line[max(0, group_match.start() - 60) : group_match.start()])
            for pos, code, name in entries:
                if not name and lead and len(entries) == 1:
                    name = lead.group(1).strip()
                name = _truncate_label(name)
                start = base + group_match.start(1) + pos
                results.append((start, start + len(code), code, f"{name}({code})" if name else code))

        for match in _CODE_REQUIREMENT_RE.finditer(line):
            if any(_spans_overlap(match.span(), span) for span in consumed):
                continue
            consumed.append(match.span())
            code_group = "code" if match.group("code") else "bare_code"
            code = match.group(code_group)
            prefix = line[: match.start()]
            # 코드가 **아직 닫히지 않은** 괄호 안에 있을 때만 괄호 안쪽을 이름으로 본다.
            # 예전엔 앞에 "("가 있기만 하면 안쪽으로 봐서, "영상정보디스플레이장치(4511189301),
            # 교육용로봇(6010621401)"의 두 번째 코드 이름이 "4511189301), 교육용로봇"이 됐다(2026-09-30 제보).
            last_open = max(prefix.rfind("("), prefix.rfind("["))
            last_close = max(prefix.rfind(")"), prefix.rfind("]"))
            is_inside = last_open > last_close
            inside_paren = prefix[last_open + 1 :].strip(" ,") if is_inside else ""
            # "[세부품명: 실물모형및전시물, 세부품명번호 10자리(6010989901)" — 괄호 안에 품명을 따로 적은 표기
            named = _INNER_ITEM_NAME_RE.search(inside_paren) if inside_paren else None
            if named:
                name = _truncate_label(named.group(1).strip())
            elif inside_paren and len(inside_paren) <= _MAX_LABEL_LEN:
                name = inside_paren
            else:
                before_paren = prefix[:last_open] if is_inside else prefix
                before_paren = _last_name_chunk(before_paren)
                # 실측: 인용부호로 감싼 품목명("조합놀이대")도 있어 대괄호/낫표류
                # 인용 문장부호와 함께 일반 인용부호("'')도 선행 문자로 떼어낸다.
                # 앞 항목과 이어 쓴 쉼표·세미콜론("…(코드1), 이름2(코드2)")도 뗀다.
                before_paren = re.sub(r"^[\s\-·,;，:：「『\"'“‘]+", "", before_paren)
                before_paren = _LEADING_CONJUNCTION_RE.sub("", before_paren)
                segments = [s for s in _LABEL_CONNECTOR_RE.split(before_paren) if s.strip()]
                name = (segments[-1] if segments else before_paren).strip()
                name = re.sub(r"^[\s,;，:：]+", "", name)  # "직접생산확인증명서 : 영상…"의 콜론
                name = name.strip("\"'“‘”’").strip()
                name = _truncate_label(name)
            start = base + match.start(code_group)
            results.append((start, start + len(code), code, f"{name}({code})" if name else code))

        for bare_match in _BARE_GROUP_RE.finditer(line):
            if any(_spans_overlap(bare_match.span(), span) for span in consumed):
                continue
            for pos, code, name in _split_group_entries(bare_match.group(1), _BARE_GROUP_ENTRY_CODE_RE):
                name = _truncate_label(name)
                start = base + bare_match.start(1) + pos
                results.append((start, start + len(code), code, f"{name}({code})" if name else code))
    return merged, results


def _or_groups(item: str) -> list[list[tuple[str, str]]]:
    """항목 안 코드 요건을 "하나만 있으면 되는" 묶음들로 나눈다. 묶음 사이는 AND다.

    - 항목에 "어느 하나"가 있으면 항목 전체가 한 묶음(OR).
    - 코드 뒤에 "또는/혹은"이 오면 그 앞뒤 코드를 한 묶음으로 잇는다.
    - "A, B 또는 C"처럼 쉼표로 이어지다 "또는"으로 끝나는 나열은 전체를 한 묶음으로.
    - 그 밖은 코드마다 따로(= 모두 보유해야 충족).
    """
    merged, located = _locate_code_requirements(item)
    if not located:
        return []
    ordered = sorted(located, key=lambda r: r[0])
    pairs = [(code, label) for _, _, code, label in ordered]
    if _OR_MARKER_RE.search(item):
        return [pairs]

    # links[i]: ordered[i]와 ordered[i+1] 사이 연결 — "or" / "comma" / "and"
    links = []
    for cur, nxt in zip(ordered, ordered[1:]):
        between = merged[cur[1] : nxt[0]]
        if _OR_LINK_RE.match(between):
            links.append("or")
        elif _COMMA_LINK_RE.match(between):
            links.append("comma")
        else:
            links.append("and")
    # 쉼표 연결은 그 뒤로 이어지는 연결이 "또는"으로 끝날 때만 OR로 승격한다.
    joined = [False] * len(links)
    carry = False
    for i in range(len(links) - 1, -1, -1):
        carry = links[i] == "or" or (links[i] == "comma" and carry)
        joined[i] = carry

    groups = [[pairs[0]]]
    for i, pair in enumerate(pairs[1:]):
        if joined[i]:
            groups[-1].append(pair)
        else:
            groups.append([pair])
    return groups


UNNAMED_LABEL = "이름 미확인"
# 문서에서 뽑은 이름표가 품목·업종명이 아니라 서류·분류 체계 이름인 경우 —
# 이름으로 쓰지 않고 다른 출처(같은 공고의 다른 표기)나 "이름 미확인"으로 넘긴다.
_GENERIC_LABEL_RE = re.compile(r"증명서|확인서|등록증|물품분류번호|세부품명|업종코드|입찰참가자격|자격을?\s*등록")
_TRAILING_CODE_RE = re.compile(r"\(([0-9]{4,10})\)$")


def named_codes(items: list[str]) -> dict[str, str]:
    """참가자격 항목들에서 "이름(코드)"로 이름까지 뽑힌 코드만 {코드: 이름표} 로 모은다.
    같은 공고 안에서 한 곳은 코드만, 다른 곳은 이름까지 적힌 경우(공고문 vs
    제안요청서) 코드만 뽑힌 쪽을 채우는 데 쓴다."""
    found: dict[str, str] = {}
    for item in items:
        for code, label in _extract_code_requirements(item):
            if label != code and not _GENERIC_LABEL_RE.search(label.rsplit("(", 1)[0]):
                found.setdefault(code, label)
    return found


def evaluate_attachment_text(
    items: list[str],
    held_codes: set[str],
    held_code_names: dict[str, str] | None = None,
    code_names: dict[str, str] | None = None,
) -> QualificationResult:
    """첨부파일 참가자격 절에서 업종코드·세부품명번호가 명시된 항목만 뽑아,
    API 판정(`evaluate`)과 같은 형태의 결과를 만든다 — 리포트에서 "자격 충족" /
    "자격 미달(이름)"로 API 기반 판정과 똑같이 보이게 하기 위함이다.

    코드가 안 붙은 일반 결격사유(나라장터 등록 여부, 부정당업자 여부, 공동수급
    구성 방식 등)는 판정 대상에서 뺀다 — 문장만 보고 "이게 자격요건이다"를
    추정하면 오탈락 위험이 크지만, 코드는 문서에 명시된 값 그대로라 비교가
    정확하다. "다음 중 어느 하나"가 있으면 그 항목 안 코드 중 하나만 있어도
    충족(OR)이다(실측: 정선군 복합문화센터 공고문 — "라"항은 품목 3개를 모두
    소지해야 하고, "바"항은 "다음 중 어느 하나"로 명시됨). 그 밖에는 "또는"으로
    이어진 코드끼리만 OR 묶음이 되고, 묶음(과 홀로 선 코드)은 전부 충족해야
    항목이 충족이다(`_or_groups`) — 예: "모두 소지: (A 또는 B), C"에서 B만
    보유하고 C가 없으면 미달.

    이름표는 ① `held_code_names`(등록증) ② `code_names`(코드 이름 사전·공고 API 정보
    등, 호출 쪽이 모아서 넘김)에 있으면 그 이름을 쓰고, 없으면 ③ 문서에서 뽑은 이름표
    ④ 같은 공고의 다른 곳에 "이름(코드)"로 적힌 이름 순으로 쓴다. 문서가 코드만 적고
    (예: "[업종코드 4440, 4442, 4444]") 어디에도 이름이 없으면 "이름 미확인(코드)"로
    두고 `unnamed_codes`에 남긴다.
    """
    held_code_names = held_code_names or {}
    seen_in_notice = named_codes(items)
    lookup = {**(code_names or {}), **held_code_names}

    def _label(code: str, label: str) -> str:
        # 사전·등록증·API에 이름이 있으면 그걸 먼저 쓴다 — 문서에서 뽑은 이름표는
        # 표기가 제각각이라 "직접생산확인증명서(7215409901)"처럼 품목명이 아닌
        # 구절이 잡히는 경우가 있었다(사용자 제보, 2026-09-28).
        if code in lookup:
            return f"{lookup[code]}({code})"
        if label != code and not _GENERIC_LABEL_RE.search(label.rsplit("(", 1)[0]):
            return label
        if code in seen_in_notice:
            return seen_in_notice[code]
        return f"{UNNAMED_LABEL}({code})"

    groups: list[LicenseGroup] = []
    missing: list[LicenseGroup] = []
    parsed_labels: dict[str, str] = {}

    name_held: list[str] = []  # 이름으로 대조해 보유 확인된 요건 (충족 표시용)
    # 보유로 채운 "A 또는 B" 묶음 — 팝업에 "아래 N개 중 1개 이상"으로 보이게 따로 남긴다(2026-10-01 요청:
    # "환경디자인(4442) 또는 종합디자인(4444)"을 둘 다 보유하면 "보유 확인" 두 줄로만 나와 "또는"인지 몰랐음)
    or_ok: list[LicenseGroup] = []
    for idx, item in enumerate(items):
        or_groups = [[(code, _label(code, label)) for code, label in bundle] for bundle in _or_groups(item)]
        if not or_groups:
            # 코드 없이 이름만 적힌 업종·직접생산 품목 — 보유 목록·코드 사전에 있는 이름으로만 판정한다
            from .text_requirements import name_bundles

            bundles = name_bundles(item, held_code_names, code_names or {})
            if not bundles:
                continue
            group_no = f"이름{idx}"
            groups.append(LicenseGroup(group_no=group_no, allowed_names=[r.label for b in bundles for r in b]))
            # 묶음(OR)끼리는 모두 필요 — 빠진 묶음마다 요건 1건 (한 덩어리로 합치면 화면에 "N개 중 1개"로 보인다)
            for b in bundles:
                if not any(r.held for r in b):
                    missing.append(LicenseGroup(group_no=group_no, allowed_names=[r.label for r in b]))
                elif len(b) > 1:
                    or_ok.append(LicenseGroup(group_no="또는", allowed_names=[r.label for r in b]))
            name_held.extend(r.label for b in bundles for r in b if r.held)
            continue

        # 코드가 적힌 항목 안에 코드 없이 이름만 적힌 하위 항목이 섞여 있으면("1) 직접생산[세부품명번호 …] 2) 산업디자인
        # [업종코드 …] 3) 소프트웨어산업 진흥법에 따른 소프트웨어사업자") 그 하위 항목은 이름으로 따로 본다
        # (2026-10-01 제보: 하남역사박물관 — 소프트웨어사업자 요건이 빠졌음). "어느 하나"면 코드 쪽과 OR라 건너뜀.
        if not _OR_MARKER_RE.search(item):
            from .text_requirements import name_bundles, sub_items

            # 하위 항목에 코드 표기 흔적이 있으면(정규식이 못 읽은 "세부품명번호-6012100201" 등) 이름 판정하지 않는다
            plain = [p for p in sub_items(item) if not _or_groups(p) and not _CODE_HINT_RE.search(p)]
            for j, part in enumerate(plain):
                bundles = name_bundles(part, held_code_names, code_names or {})
                if not bundles:
                    continue
                sub_no = f"이름{idx}-{j}"
                groups.append(LicenseGroup(group_no=sub_no, allowed_names=[r.label for b in bundles for r in b]))
                for b in bundles:
                    if not any(r.held for r in b):
                        missing.append(LicenseGroup(group_no=sub_no, allowed_names=[r.label for r in b]))
                    elif len(b) > 1:
                        or_ok.append(LicenseGroup(group_no="또는", allowed_names=[r.label for r in b]))
                name_held.extend(r.label for b in bundles for r in b if r.held)

        group_no = str(idx)
        all_labels = [label for bundle in or_groups for _, label in bundle]
        groups.append(LicenseGroup(group_no=group_no, allowed_names=all_labels))
        for bundle in or_groups:
            for code, label in bundle:
                parsed_labels.setdefault(code, label)

        # 묶음 안에서는 하나만 보유해도 충족, 묶음끼리는 전부 충족해야 항목 충족.
        # "[업종코드 4440, 4442, 4444]"처럼 "또는" 없이 나열한 코드는 모두 필요(2026-10-01 사용자 확정) — 빠진 묶음마다
        # 요건 1건으로 둔다. 한 덩어리로 합치면 화면에 "아래 2개 중 1개 이상"(또는)으로 잘못 보였다.
        for bundle in or_groups:
            if not any(code in held_codes for code, _ in bundle):
                missing.append(LicenseGroup(group_no=group_no, allowed_names=[label for _, label in bundle]))
            elif len(bundle) > 1:
                or_ok.append(LicenseGroup(group_no="또는", allowed_names=[label for _, label in bundle]))

    if not groups:
        return QualificationResult(total_groups=0, missing_groups=[], passes=True, checked=False)

    # "충족된 자격" 표시는 그룹 단위 AND/OR 판정과 별개로, 참가자격 절 원문
    # 전체에서 보유 코드가 실제로 나오는지 직접 훑어 만든다 — 위 판정 로직이
    # 놓친 코드(괄호 안쪽에 코드가 여러 개 나열되는 등)도 여기서는 다 잡힌다.
    full_text = "\n".join(items)
    found_codes = dict.fromkeys(_STANDALONE_CODE_RE.findall(full_text))
    satisfied_names = [
        f"{held_code_names[code]}({code})" if code in held_code_names else parsed_labels.get(code, code)
        for code in found_codes
        if code in held_codes
    ]
    satisfied_names += [n for n in dict.fromkeys(name_held) if n not in satisfied_names]
    satisfied_groups = or_ok + ([LicenseGroup(group_no="held", allowed_names=satisfied_names)] if satisfied_names else [])

    return QualificationResult(
        total_groups=len(groups),
        missing_groups=missing,
        passes=len(missing) <= MAX_ALLOWED_MISSING_QUALIFICATIONS,
        checked=True,
        satisfied_groups=satisfied_groups,
        # 미보유로 걸렸는데 이름을 못 찾은 코드만 알린다 — 보유 코드는 충족
        # 표시에서 등록증 이름을 쓰므로 사전에 없어도 문제없다.
        unnamed_codes=list(
            dict.fromkeys(
                _TRAILING_CODE_RE.search(name).group(1)
                for g in missing
                for name in g.allowed_names
                if name.startswith(UNNAMED_LABEL)
            )
        ),
    )


def merge_results(base: QualificationResult, extra: QualificationResult) -> QualificationResult:
    """API 판정(`evaluate`) 위에 첨부파일 판정(`evaluate_attachment_text`)을 겹친다.

    **API가 자격정보를 줬다고 해서 그게 요건 전부인 것이 아니다.** 면허제한정보
    오퍼레이션에는 면허명·허용업종만 있고 세부품명번호(직접생산확인 대상 품목)는
    필드 자체가 없다(`fields.LICENSE_LIMIT_FIELDS`). 그래서 API 판정만 보면
    품목 요건이 통째로 빠진다 — 실측: 단양군 미디어아트 공고(R26BK01731335)는
    API가 업종 4그룹을 주고 전부 충족이라 했지만, 첨부 공고문은 직접생산확인증명서로
    조명용제어장치(3912110702)를 요구했고 그건 미보유다.

    그래서 첨부파일 판정을 API 판정의 *대체*가 아니라 *추가*로 쓴다. 한쪽이라도
    미충족이면 미충족이다. 같은 요건이 양쪽에 다 잡히면(업종 항목은 보통 그렇다)
    그룹 수는 중복 집계되지만, 판정과 표시에 쓰는 건 미충족 목록이라 문제되지 않는다.
    """
    if not extra.checked:
        return base
    if not base.checked:
        return extra

    missing = base.missing_groups + extra.missing_groups
    return QualificationResult(
        total_groups=base.total_groups + extra.total_groups,
        missing_groups=missing,
        passes=len(missing) <= MAX_ALLOWED_MISSING_QUALIFICATIONS,
        checked=True,
        satisfied_groups=base.satisfied_groups + extra.satisfied_groups,
    )


def load_held_names(held_config: dict) -> list[str]:
    names: list[str] = []
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_config.get(key, []):
            name = str(entry.get("name", "")).strip()
            if name:
                names.append(name)
    return names


def load_held_codes(held_config: dict) -> set[str]:
    codes: set[str] = set()
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_config.get(key, []):
            code = str(entry.get("code", "")).strip()
            if code:
                codes.add(code)
    return codes


_API_NAME_CODE_RE = re.compile(r"^(.+?)\s*/\s*([0-9]{4,10})$")


def api_code_names(groups: list[LicenseGroup]) -> dict[str, str]:
    """면허제한정보 API 그룹의 "업종명/코드" 표기에서 {코드: 이름}을 뽑는다
    (lcnsLmtNm이 실측상 "실내건축공사업/4990" 형태로 내려온다)."""
    found: dict[str, str] = {}
    for g in groups:
        for name in g.allowed_names:
            match = _API_NAME_CODE_RE.match(name.strip())
            if match:
                found.setdefault(match.group(2), match.group(1).strip())
    return found


def load_code_names(codes_config: dict, code_names_config: dict | None = None) -> dict[str, str]:
    """코드 이름 사전. `codes.json`(매칭용 코드 목록)의 이름 + `code_names.json`
    (사람이 추가하는 사전). 같은 코드면 code_names.json이 우선."""
    names: dict[str, str] = {}
    for key in ("productCodes", "industryCodes"):
        for entry in codes_config.get(key, []):
            code = str(entry.get("code", "")).strip()
            name = str(entry.get("name", "")).strip()
            if code and name:
                names.setdefault(code, name)
    for code, name in (code_names_config or {}).get("names", {}).items():
        if str(code).strip() and str(name).strip():
            names[str(code).strip()] = str(name).strip()
    return names


def load_held_code_names(held_config: dict) -> dict[str, str]:
    """코드 → 등록증에 적힌 이름. `evaluate_attachment_text`가 "충족" 판정을 낼 때
    쓴다 — 코드가 이미 held_codes에 있다는 걸 확인한 뒤라 여기 lookup은 항상
    성공한다. 첨부문서 원문에서 파싱한 이름표(문서 표기가 제각각이라 fragile함)
    대신 등록증 원문 표기를 그대로 보여줄 수 있다."""
    names: dict[str, str] = {}
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_config.get(key, []):
            code = str(entry.get("code", "")).strip()
            name = str(entry.get("name", "")).strip()
            if code and name:
                names[code] = name
    return names


def fetch_license_groups(
    client: DataGoKrClient, begin: str, end: str
) -> tuple[dict[str, list[LicenseGroup]], str | None]:
    """조회기간 전체의 면허제한정보를 받아 공고번호별로 묶어 반환한다.

    실패해도 예외를 올리지 않는다 — 호출측에서 fail-open으로 처리하기 위함.

    주의: 이 오퍼레이션은 조회기간이 너무 길면 resultCode=07(입력범위값 초과)로
    거부한다. 실측상 30일은 되고 그 이상(180일은 물론 ~100일도)은 막힌다 —
    정확한 상한은 모른다. `client.fetch_all_pages_chunked`가 안전한 범위
    단위로 나눠 호출하므로 LOOKBACK_DAYS를 30일보다 길게 잡아도 된다.
    그래도 한 청크가 실패하면 이 기간 내 **모든** 공고가 "자격정보
    없음(판정 보류)"로 처리된다(`scripts/inspect_notice.py`로 먼저 범위를
    확인해볼 수 있다).
    """
    try:
        raw_items = client.fetch_all_pages_chunked(
            F.BID_NOTICE_BASE_URL,
            F.LICENSE_LIMIT_OPERATION,
            {"inqryDiv": "1"},
            begin,
            end,
            "면허제한정보",
        )
    except ApiError as err:
        return {}, str(err)
    return group_license_rows(raw_items), None


def fetch_region_limits(
    client: DataGoKrClient, begin: str, end: str
) -> tuple[dict[str, list[str]], str | None]:
    """참가가능지역을 공고번호별로 수집한다 (판정에는 쓰지 않고 정보로만 표시).

    협상계약은 용역·물품 비중이 높은데 이 API는 용역·물품에서 대부분 값을 주지 않는다.
    비어 있는 것이 정상이며, "제한 없음"이 아니라 "정보 없음"으로 다뤄야 한다.
    """
    try:
        raw_items = client.fetch_all_pages_chunked(
            F.BID_NOTICE_BASE_URL,
            F.REGION_LIMIT_OPERATION,
            {"inqryDiv": "1"},
            begin,
            end,
            "참가가능지역",
        )
    except ApiError as err:
        return {}, str(err)

    by_notice: dict[str, list[str]] = {}
    for item in raw_items:
        notice_no = F.pick_by(item, F.REGION_LIMIT_FIELDS, "notice_no")
        region = F.pick_by(item, F.REGION_LIMIT_FIELDS, "region_name")
        if notice_no and region:
            by_notice.setdefault(notice_no, []).append(region)
    return by_notice, None


# ── 공동수급 (정보 수집 전용) ───────────────────────────────────

_JOINT_PATTERN = re.compile(r"^\((?P<submit>전자|수기|없음)\)(?P<exec>.+)$")


@dataclass
class JointSupply:
    allowed: bool
    submit_type: str | None  # 전자 / 수기 / 없음 (공동수급협정서 제출 방식)
    exec_type: str | None  # 공동이행 / 분담이행 / 공동이행 또는 분담이행 / 혼합방식 / 공동수급불허
    raw_value: str | None

    @property
    def label(self) -> str:
        if self.raw_value is None:
            return "정보 없음"
        return self.exec_type or self.raw_value


def parse_joint_supply(value: str | None) -> JointSupply:
    """cmmnSpldmdMethdNm 을 '(제출방식)이행방식' 구조로 분해한다.

    실측 값 예: "(없음)공동수급불허", "(전자)공동이행 또는 분담이행", "(수기)분담이행"
    """
    if not value:
        return JointSupply(allowed=False, submit_type=None, exec_type=None, raw_value=None)

    text = value.strip()
    match = _JOINT_PATTERN.match(text)
    submit_type = match.group("submit") if match else None
    exec_type = match.group("exec").strip() if match else text

    allowed = "불허" not in exec_type
    return JointSupply(allowed=allowed, submit_type=submit_type, exec_type=exec_type, raw_value=text)
