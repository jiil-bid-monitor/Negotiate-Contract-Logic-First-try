"""공고 폴더의 회사준비서류 — 표본(1억미만_사업폴더_취합_20261028)을 만든 규칙 그대로. AI 없이 규칙으로만.

공고문에서 제출 서류 이름을 찾아 항목마다 폴더를 만들고, 최신 회사 서류 중 쓸 수 있는 것만 복사한다.
유효기간이 지났거나 재발급이 필요한 서류는 넣지 않고 체크리스트·공고정보 요약에 적는다.
현재 대표자·대표 변경일은 공개 저장소에 두지 않고 회사 프로필(`form_fill.load_company`)에서 받는다.
재발급 기준(발급 3개월 등)은 관행 — 표본과 같은 임시값이다(10/29, 차장님 확인 전).
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date, timedelta
from pathlib import Path

from .docs import LATEST, LIST_JSON

log = logging.getLogger("under100m")

PREP_DIR = "회사준비서류"
BASIS = "_공고기준.json"
CHECKLIST = "준비서류_체크리스트.txt"
FRESH_DAYS = 90

# (폴더 이름, 서류 이름 정규식, 분류) — 분류: 증명서류(발급받는 것) / 작성서류(회사가 만드는 것) / 보증·납부
CATALOG = [
    ("사업자등록증", r"사업자\s*등록\s*증", "증명서류"),
    ("법인등기부등본", r"법인\s*등기\s*부?\s*등\s*본|등기사항\s*전부\s*증명서", "증명서류"),
    ("인감증명서", r"(법인\s*)?인감\s*증명\s*서", "증명서류"),
    ("중소기업·소상공인확인서", r"(중소\s*기업|소기업|소상공인)\s*[·ㆍ‧,및\s]*(소상공인\s*)?확인서", "증명서류"),
    ("직접생산확인증명서", r"직접\s*생산\s*확인\s*증명", "증명서류"),
    ("국세·지방세 납세증명서", r"(국세|지방세)\s*(및\s*지방세\s*)?(완납|납세)\s*증명", "증명서류"),
    ("4대보험 가입증명", r"(4대|사대)\s*보험|건강\s*보험\s*(자격\s*득실|가입자\s*명부)|국민\s*연금\s*(가입|사업장)", "증명서류"),
    ("신용평가등급확인서", r"신용\s*평가\s*등급|기업\s*신용\s*등급", "증명서류"),
    ("재무제표·경영상태", r"재무\s*제표|재무\s*상태\s*표|손익\s*계산서|경영\s*상태\s*(평가|확인)", "증명서류"),
    ("업종 등록증·면허 사본", r"(공사업|전문회사|제작업|업종|사업자)\s*(등록|신고)\s*(수첩|필증|확인서)|(공사업|전문회사|제작업|업종)\s*(등록|신고)\s*증|면허\s*(수첩|사본|증)", "증명서류"),
    ("실적증명서(발급기관 확인)", r"실적\s*증명\s*서|실적\s*증명원|이행\s*실적", "증명서류"),
    ("기술자 경력증명서·자격증 사본", r"(기술자|기술인)\s*경력\s*(증명|수첩)|경력\s*증명\s*서|자격\s*증\s*(사본|명)|국가\s*기술\s*자격", "증명서류"),
    ("여성·장애인·사회적기업 확인서", r"(여성\s*기업|장애인\s*기업|사회적\s*기업|창업\s*기업)\s*(확인서|인증서)", "증명서류"),
    ("설계내역서·산출내역서", r"(설계|산출|공사|용역|입찰|가격)\s*내역\s*서|내역\s*서\s*제출|산출\s*근거", "작성서류"),
    ("견적서", r"견적\s*서", "작성서류"),
    ("단가조사서·비교견적", r"단가\s*조사|비교\s*견적|가격\s*조사|거래\s*실례", "작성서류"),
    ("제안서(정성)", r"(기술\s*)?제안\s*서\s*(\d+\s*부|및|원본|사본|제출|작성)|정성\s*제안", "작성서류"),
    ("정량 제안서·평가 자료", r"정량\s*(적\s*)?(평가|제안)", "작성서류"),
    ("가격제안서·가격입찰", r"가격\s*(제안|입찰)\s*서", "작성서류"),
    ("제안 발표 자료", r"(제안\s*)?발표\s*(자료|파일|용)", "작성서류"),
    ("디자인 시안·도면", r"(디자인\s*)?시안|조감도|투시도|도면\s*(제출|작성)", "작성서류"),
    ("공정표·수행계획", r"(예정\s*)?공정\s*표|수행\s*계획\s*서", "작성서류"),
    ("입찰·계약 보증서", r"(입찰|계약|하자)\s*보증\s*(서|금)", "보증·납부"),
    ("인지세·수입인지", r"수입\s*인지|인지\s*세", "보증·납부"),
]
DESC = {
    "사업자등록증": "사본",
    "법인등기부등본": "법인 등기사항전부증명서, 인터넷등기소 발급",
    "인감증명서": "법인 인감증명서",
    "중소기업·소상공인확인서": "중소기업현황정보시스템 발급, 유효기간 확인",
    "직접생산확인증명서": "공공구매종합정보망 발급, 유효기간 확인",
    "국세·지방세 납세증명서": "국세 홈택스·지방세 위택스 발급",
    "4대보험 가입증명": "건강보험 자격득실확인서·국민연금 가입증명 중 하나",
    "신용평가등급확인서": "신용평가기관 발급, 유효기간 내",
    "재무제표·경영상태": "최근 결산 재무제표(재무상태표·손익계산서)",
    "업종 등록증·면허 사본": "공고가 요구한 업종 등록증·신고증 사본",
    "실적증명서(발급기관 확인)": "발주기관에 발급 요청(시간이 걸리니 미리)",
    "기술자 경력증명서·자격증 사본": "참여 인력 경력증명서(협회 발급)·자격증 사본",
    "여성·장애인·사회적기업 확인서": "해당 시",
    "설계내역서·산출내역서": "품목별 수량·단가·금액",
    "견적서": "자재·외주 견적(내역서 단가 근거)",
    "단가조사서·비교견적": "타사 견적·거래 가격 조사",
    "제안서(정성)": "제안요청서 목차대로 작성",
    "정량 제안서·평가 자료": "정량평가 항목별 증빙 묶음",
    "가격제안서·가격입찰": "나라장터 전자입찰로 제출",
    "제안 발표 자료": "발표용 PPT",
    "디자인 시안·도면": "공고가 요구한 시안·도면",
    "공정표·수행계획": "사업 일정·수행 계획",
    "입찰·계약 보증서": "보증기관 발급",
    "인지세·수입인지": "전자수입인지",
}
# 공고문에 없어도 늘 넣는 작성서류 — 사용자 지정 3종 + 지일 과거 1억 미만 폴더에서 자주 만든 2종(표본과 같음)
USUAL = ("설계내역서·산출내역서", "견적서", "단가조사서·비교견적", "제안 발표 자료", "제안서(정성)")
# 체크리스트 항목 → 회사 서류 종류(여러 개면 모두)
DOC_KINDS = {
    "사업자등록증": ["사업자등록증"],
    "법인등기부등본": ["법인등기사항전부증명서"],
    "인감증명서": ["법인인감증명서"],
    "중소기업·소상공인확인서": ["중소기업·소상공인확인서"],
    "직접생산확인증명서": ["직접생산확인증명서"],
    "국세·지방세 납세증명서": ["국세 납세증명서", "지방세 납세증명서"],
    "4대보험 가입증명": ["4대보험 가입증명", "4대보험 완납증명서"],
    "신용평가등급확인서": ["신용평가등급확인서"],
    "재무제표·경영상태": ["재무제표"],
    "업종 등록증·면허 사본": ["실내건축공사업 등록증", "산업디자인전문회사 신고확인증", "공공디자인전문회사 신고증", "소프트웨어사업자 신고확인서",
                       "비디오물제작업 신고증", "정보통신공사업 등록증", "업종 등록증"],
}
VALID_KINDS = {"기술혁신형 중소기업(이노비즈) 확인서", "경영혁신형 중소기업(메인비즈) 확인서", "신용평가등급확인서", "4대보험 완납증명서",
               "국세 납세증명서", "지방세 납세증명서", "중소기업·소상공인확인서", "직접생산확인증명서"}
FRESH_KINDS = {"법인인감증명서", "법인등기사항전부증명서", "사업자등록증명", "4대보험 가입증명", "4대보험 완납증명서"}
PERMANENT_KINDS = {"사업자등록증", "경쟁입찰참가자격등록증", "사용인감계", "실내건축공사업 등록증", "금속구조물·창호공사업 등록증",
                   "산업디자인전문회사 신고확인증", "공공디자인전문회사 신고증", "소프트웨어사업자 신고확인서", "비디오물제작업 신고증",
                   "정보통신공사업 등록증", "업종 등록증"}
USE, RENEW, CHECK = "그대로 사용", "재발급 필요", "확인 필요"

_SUBMIT_CTX = re.compile(r"제출|구비|첨부|사본|1\s*부|원본|증빙|발급")
_NOT_REQ = re.compile(r"제출\s*하지\s*않|첨부\s*하지\s*않|생략|면제|대체할\s*수|해당\s*(없|자에\s*한)|요구\s*하지|갈음|확약|포함되어")
# 낙찰·계약 뒤에 내는 서류(착수계·공정표 등) — 입찰 준비 서류 아님
_AFTER_AWARD = re.compile(r"착수|계약\s*(체결|통보|후|이후)|계약\s*상대자|낙찰자\s*(로\s*선정|는|가)|준공|기성|하도급|설계\s*변경|승인을\s*받|완료\s*예정일|중간\s*설계")
_ISSUE_AFTER = re.compile(r"(입찰\s*)?공\s*고\s*(일|게\s*시\s*일)?\s*(이\s*후|후|다\s*음\s*날)\s*(에\s*)?(발\s*급|발\s*행)"
                          r"|공\s*고\s*일\s*(전|이\s*전|기\s*준)\s*\d+\s*(개월|일)\s*이\s*내\s*(에\s*)?(발\s*급|발\s*행)"
                          r"|공\s*고\s*일\s*(현\s*재|기\s*준)\s*(으로\s*)?(발\s*급|발\s*행)")
# 목록 전체를 가리키는 말 — "※ 상기 입찰관련서류는 입찰공고 이후에 발급한 자료를 제출"
_ALL_DOCS = re.compile(r"상\s*기|위\s*(의\s*)?서\s*류|제\s*출\s*서\s*류|입\s*찰\s*관\s*련\s*(참\s*고\s*)?서\s*류|구\s*비\s*서\s*류|증\s*빙\s*서\s*류|모\s*든\s*서\s*류|각\s*종\s*서\s*류")
_SENTENCE = re.compile(r"\n|(?<=[.다])\s")
AFTER_NOTICE = "공고일 이후 발급분"


def find_items(texts: list[str]) -> dict[str, tuple[str, str]]:
    """공고 문서에서 제출 서류 항목 → (분류, 그 문장)."""
    found: dict[str, tuple[str, str]] = {}
    for text in texts:
        for line in _SENTENCE.split(text):
            sentence = re.sub(r"\s+", " ", line).strip()
            if len(sentence) < 4 or len(sentence) > 220 or not _SUBMIT_CTX.search(sentence):
                continue
            for item, pattern, kind in CATALOG:
                if item not in found and re.search(pattern, sentence) and not _NOT_REQ.search(sentence) and not _AFTER_AWARD.search(sentence):
                    found[item] = (kind + (" (대리인 접수 시)" if "대리인" in sentence else ""), sentence[:150])
    return found


def issue_after_all(texts: list[str]) -> bool:
    """공고 전체에 걸린 '공고일 이후 발급' 문장이 있는지(목록 전체를 가리킬 때만)."""
    return any(_ISSUE_AFTER.search(s) and _ALL_DOCS.search(s)
               for text in texts for s in (re.sub(r"\s+", " ", line) for line in _SENTENCE.split(text)))


def hints(sentence: str) -> list[str]:
    """공고 문장에서 부수·원본 여부·발급 시점 같은 조건만."""
    out = []
    copies = re.search(r"(\d+)\s*부", sentence)
    if copies:
        out.append(f"{copies.group(1)}부")
    if re.search(r"원본\s*대조", sentence):
        out.append("원본대조필")
    elif "원본" in sentence:
        out.append("원본")
    if re.search(r"인감\s*(날인|도장)|날인", sentence):
        out.append("인감 날인")
    if _ISSUE_AFTER.search(sentence):
        out.append(AFTER_NOTICE)
    if re.search(r"유효\s*기간", sentence):
        out.append("유효기간 내")
    if "USB" in sentence:
        out.append("USB")
    return out


def base_kind(kind: str) -> str:
    return "직접생산확인증명서" if kind.startswith("직접생산확인증명서") else kind


def category(kind: str, line: str = "") -> str:
    """서류 구분 — 공고가 공고일 이후 발급분을 요구하면 기간 없는 서류 말고는 그쪽으로."""
    base = base_kind(kind)
    if AFTER_NOTICE in line and base != "실적증명서":
        return "공고일 이후 발급"
    if base in PERMANENT_KINDS or base == "실적증명서":
        return "그대로 둠"
    if base in FRESH_KINDS:
        return "3개월 이내 발급"
    if base in VALID_KINDS or base == "재무제표":
        return "유효기간 갱신"
    return "-"


def _judge_self(record: dict, ref: str, rule: dict) -> tuple[str, str]:
    """서류 자체 기준 — (상태, 사유). 재발급 필요면 폴더에 안 넣고, 확인 필요면 넣되 표시한다."""
    kind, ref_day = base_kind(record["종류"]), date.fromisoformat(ref)
    end, issued = record.get("유효기간") or "", record.get("발급일") or ""
    ceo, changed = rule.get("현재대표자"), rule.get("대표변경일")
    if kind == "실적증명서":
        return USE, "유효기간 없음 — 그 사업의 실적 증명(발주기관 발급)"
    if ceo and record.get("대표자") and record["대표자"] != ceo:
        return RENEW, f"대표자 {record['대표자']}로 된 서류 — 대표 {ceo} 반영본 필요"
    if ceo and changed and kind in PERMANENT_KINDS and record.get("대표자") != ceo and issued < changed:
        return RENEW, f"{changed} 대표 변경 전 발급·갱신 — {ceo} 대표 기준으로 새로 발급·출력"
    if kind in VALID_KINDS and end:
        return (RENEW, f"유효기간 {end} 지남") if end < ref else (USE, f"유효기간 {end}까지")
    if kind in FRESH_KINDS:
        if issued < (ref_day - timedelta(days=FRESH_DAYS)).isoformat():
            return RENEW, "발급 3개월 넘음(보통 3개월 이내 발급분 요구)"
        if record.get("현장"):
            return RENEW, f"특정 현장({record['현장'][:20]})용 서류 — 회사 전체용으로 새로 발급"
        return USE, "발급 3개월 이내"
    if kind in VALID_KINDS:
        return CHECK, "유효기간을 못 읽음 — 원본에서 확인"
    if kind in PERMANENT_KINDS:
        return USE, "기간 없음 — 상호·대표자·주소·업종이 바뀌었으면 새로"
    if kind == "재무제표":  # 직전 연도 결산은 보통 3월 말 확정
        need = ref_day.year - 1 if ref_day.month >= 4 else ref_day.year - 2
        year = (end or issued)[:4]
        return (RENEW, f"{year}년 결산 — {need}년 결산 재무제표 필요") if year < str(need) else (USE, f"{year}년 결산")
    return CHECK, "기준 없음 — 원본 확인"


def judge(record: dict, line: str, ref: str, posted: str | None, rule: dict) -> tuple[str, str]:
    """서류 자체 기준을 먼저 보고, 공고가 공고일 이후 발급분을 요구하면 그다음 공고일과 비교."""
    state, why = _judge_self(record, ref, rule)
    if state == RENEW or AFTER_NOTICE not in line or base_kind(record["종류"]) == "실적증명서":
        return state, why
    if not posted:
        return CHECK, "공고일을 못 찾음 — 공고일 이후 발급분인지 확인"
    if (record.get("발급일") or "") < posted:
        return RENEW, f"공고일({posted}) 이후 발급분 필요 — 새로 발급·출력"
    return state, why


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def checklist_rows(texts: list[str], form_names: list[str]) -> tuple[list[tuple[str, str, str]], set[str]]:
    """(항목, 분류, 근거 문장) 목록과 서식 파일로 이미 있는 항목."""
    items = find_items(texts)
    forms = [re.sub(r"\s", "", name) for name in form_names]
    key = lambda item: re.sub(r"[·()\s]|확인서$|증명서$", "", item)[:4]  # noqa: E731
    has_form = {item for item in items if any(key(item) in form for form in forms)}
    rows = [(item, kind, sentence) for item, (kind, sentence) in items.items() if item not in has_form]
    have = {row[0] for row in rows} | has_form
    rows += [(item, "작성서류", "") for item in USUAL if item not in have]
    order = {"작성서류": 0, "증명서류": 1, "보증·납부": 2}
    rows.sort(key=lambda row: (order.get(row[1].split(" (")[0], 3), row[0]))
    return rows, has_form


def prepare(prep: Path | None, texts: list[str], form_names: list[str], docs_root: Path, deadline: str | None,
            posted: str | None, today: date, rule: dict | None = None) -> tuple[list[tuple[str, str, str]], str]:
    """체크리스트를 만들고 쓸 수 있는 회사 서류를 복사한다. `prep`이 None이면 판단만 — 재공고 때 담당자가 넣은 파일을 안 건드리려고.
    돌려주는 값: (새로 준비할 것 [(항목, 파일, 사유)], 판단 기준일)."""
    ref = max(deadline or today.isoformat(), today.isoformat())
    source = docs_root / LATEST if (docs_root / LATEST).is_dir() else docs_root
    records = _load(docs_root / LIST_JSON, [])
    rule = rule or {}
    latest: dict[str, dict] = {}
    for record in records:
        if record["종류"] not in latest or record["발급일"] > latest[record["종류"]]["발급일"]:
            latest[record["종류"]] = record
    rows, has_form = checklist_rows(texts, form_names)
    after_all = issue_after_all(texts)
    if prep is not None:
        prep.mkdir(parents=True, exist_ok=True)
        (prep / BASIS).write_text(json.dumps({"마감": deadline, "공고일": posted}, ensure_ascii=False), encoding="utf-8")
    today_text = today.isoformat()
    note = f" (제출 마감 {deadline})" if deadline and deadline >= today_text else f" (마감 {deadline} 지난 공고 — 오늘 기준)" if deadline else " (마감일 못 찾음 — 오늘 기준)"
    lines = ["회사 준비 서류 체크리스트 (자동 생성 — 별지 서식 말고 회사가 따로 만들거나 발급받을 것)", f"※ 서류 판단 기준일 {ref}{note}", "",
             "공고문 제출서류 목록과 꼭 대조하세요.", ""]
    need: list[tuple[str, str, str]] = []
    group_now = None
    for no, (item, kind, sentence) in enumerate(rows, 1):
        sub = prep / f"{no:02d}_{item}".replace("/", "·") if prep is not None else None
        if sub is not None:
            sub.mkdir(exist_ok=True)
        group = kind.split(" (")[0]
        if group != group_now:
            lines += ([""] if group_now else []) + [f"■ {group}"]
            group_now = group
        hint = hints(sentence)
        if "대리인" in kind:
            hint.insert(0, "대리인 접수 시")
        if after_all and group == "증명서류" and AFTER_NOTICE not in hint:
            hint.append(AFTER_NOTICE)
        line = f"[ ] {no:02d}. {item}" + (f" — {DESC[item]}" if DESC.get(item) else "") + (f" ({', '.join(hint)})" if hint else "")
        lines.append(line)
        if item not in DOC_KINDS:
            continue
        docs = [latest[k] for want in DOC_KINDS[item] for k in sorted(latest) if base_kind(k) == want]
        if any(r["종류"].startswith("직접생산확인증명서(") for r in docs):  # 품목을 못 읽은 예전 증명서는 품목별 것이 있으면 뺌
            docs = [r for r in docs if r["종류"] != "직접생산확인증명서"]
        if not docs:
            need.append((item, "", "회사 서류에 없음 — 직접 준비"))
            lines.append("        → 회사 서류에 없음 — 직접 준비")
        for record in docs:
            state, why = judge(record, line, ref, posted, rule)
            label = category(record["종류"], line)
            name = record["파일"]
            if state == RENEW:
                need.append((item, name, f"재발급 필요 [{label}] — {why}"))
                lines.append(f"        → [{label}] {name} ※ 재발급 필요 — {why}, 폴더에 안 넣음")
                continue
            if sub is not None:
                try:
                    shutil.copyfile(source / name, sub / name)
                except OSError as err:
                    log.warning("회사 서류 복사 실패 %s: %s", name, err)
                    need.append((item, name, "파일을 찾지 못함 — 회사 서류 폴더 확인"))
                    lines.append(f"        → {name} ※ 파일을 찾지 못함 — 회사 서류 폴더 확인")
                    continue
            end = record.get("유효기간") if base_kind(record["종류"]) in VALID_KINDS else ""
            lines.append(f"        → [{label}] {name}(발급 {record.get('발급일')}{f', 유효 ~{end}' if end else ''})" + (f" ※ 확인 필요 — {why}" if state == CHECK else ""))
    lines.append("")
    if has_form:
        lines.append("서식 파일로 이미 있는 것(서식자동분리): " + ", ".join(sorted(has_form)))
    if prep is not None:
        (prep / CHECKLIST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return need, ref
