"""수집 결과를 담는 자료구조."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from . import fields as F


@dataclass
class Notice:
    """공고 1건 (공고번호 + 차수 단위)."""

    work_type: str
    notice_no: str
    notice_ord: str
    title: str
    notice_institution: str | None = None
    demand_institution: str | None = None
    detail_url: str | None = None

    award_method: str | None = None
    contract_method: str | None = None
    bid_method: str | None = None

    notice_kind: str | None = None
    re_notice_yn: str | None = None
    change_reason: str | None = None

    estimated_price: float | None = None
    assigned_budget: float | None = None

    posted_at: str | None = None
    qualification_deadline: str | None = None
    bid_deadline: str | None = None
    opening_at: str | None = None
    estimate_price_method: str | None = None

    joint_method_name: str | None = None
    joint_method_code: str | None = None
    joint_agreement_deadline: str | None = None
    joint_member_region_limit: str | None = None

    industry_limit_yn: str | None = None
    industry_text: str | None = None
    product_class_no: str | None = None
    product_class_name: str | None = None
    # 구매대상물품 목록의 세부품명번호 전부(대표 세부품명 포함) — 품명코드 매칭은 이 중 하나만 맞아도 된다
    product_codes: list[str] = field(default_factory=list)

    attachments: list[dict[str, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    # 본공고 / 사전규격. 사전규격은 공고번호 자리에 사전규격등록번호가 들어가고,
    # 입찰 마감 대신 의견등록 마감이 있으며, 낙찰방법·면허제한·공동수급 정보가 없다.
    kind: str = "본공고"
    opinion_deadline: str | None = None
    # 사전규격이 이미 본공고로 나갔으면 그 입찰공고번호들
    linked_bid_notices: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.work_type, self.notice_no, self.notice_ord)

    @property
    def linked_system(self) -> dict[str, str] | None:
        """나라장터엔 제목·일정만 있고 규격서·상세는 다른 조달시스템에 있는 공고 → {name, url}.

        2026-10-12 사용자 제보: 국방부(국군재정관리단)가 국방전자조달(d2b.go.kr)에 올린 사전규격은
        나라장터에 "연계기관 공개 규격"으로 첨부 0건, 상세는 d2b 링크만 있다 — 첨부를 못 읽어 자격 미확인.
        10/12 실응답 진단: 응답에 d2b 주소 칸은 없고, 국방 기관 사전규격은 참조번호(refNo)가 "PS2026…"로 왔다. 그래서
        ① 응답 어느 칸에든 d2b.go.kr 주소가 있으면 그 주소 ② 첨부가 0건이고 참조번호가 PS…이거나 국방 기관이면 d2b 사전규격 목록.
        """
        for value in self.raw.values():
            if isinstance(value, str):
                m = _D2B_URL.search(value)
                if m:
                    return {"name": D2B_NAME, "url": m.group(0)}
        if self.attachments or self.kind != PRESPEC_KIND:
            return None
        if _D2B_REF_NO.match(str(self.raw.get("refNo") or "")):
            return {"name": D2B_NAME, "url": D2B_PRESPEC_LIST}
        institutions = f"{self.notice_institution or ''} {self.demand_institution or ''}"
        if _DEFENSE_INSTITUTION.search(institutions):
            return {"name": D2B_NAME, "url": D2B_PRESPEC_LIST}
        return None

    @property
    def budget(self) -> float | None:
        """금액 기준을 하나로 통일한다. 추정가격 우선, 없으면 배정예산."""
        return self.estimated_price if self.estimated_price else self.assigned_budget

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PRESPEC_KIND = "사전규격"
D2B_NAME = "국방전자조달"
D2B_PRESPEC_LIST = "https://www.d2b.go.kr/psb/plan/servicePreStdList.do"
G2B_PRESPEC_URL = "https://www.g2b.go.kr/link/PRVA004_02/single/?bfSpecRegNo={no}"
# 국방전자조달 공고의 참조번호 — 실응답(10/12)에서 국방부·해군 사전규격이 "PS2026UMM39791"처럼 왔다
_D2B_REF_NO = re.compile(r"^PS\d{4}[A-Z]")
_D2B_URL = re.compile(r"https?://(?:www\.)?d2b\.go\.kr[^\s\"'<>]*")
_DEFENSE_INSTITUTION = re.compile(r"국방부|국군|방위사업청|육군|해군|공군|해병대")


def _product_codes(main: str | None, listed: str | None) -> list[str]:
    codes = [main.strip()] if main and main.strip() else []
    codes += [code for code, _ in F.parse_product_list(listed)]
    return list(dict.fromkeys(codes))


def notice_from_raw(raw: dict[str, Any], work_type: str) -> Notice:
    clean = F.strip_personal_fields(raw)
    return Notice(
        work_type=work_type,
        notice_no=F.pick_field(clean, "notice_no") or "",
        notice_ord=F.pick_field(clean, "notice_ord") or "000",
        title=F.pick_field(clean, "title") or "(제목 없음)",
        notice_institution=F.pick_field(clean, "notice_institution"),
        demand_institution=F.pick_field(clean, "demand_institution"),
        detail_url=F.pick_field(clean, "detail_url"),
        award_method=F.pick_field(clean, "award_method"),
        contract_method=F.pick_field(clean, "contract_method"),
        bid_method=F.pick_field(clean, "bid_method"),
        notice_kind=F.pick_field(clean, "notice_kind"),
        re_notice_yn=F.pick_field(clean, "re_notice_yn"),
        change_reason=F.pick_field(clean, "change_reason"),
        estimated_price=F.pick_number(clean, "estimated_price"),
        assigned_budget=F.pick_number(clean, "assigned_budget"),
        posted_at=F.pick_field(clean, "posted_at"),
        qualification_deadline=F.pick_field(clean, "qualification_deadline"),
        bid_deadline=F.pick_field(clean, "bid_deadline"),
        opening_at=F.pick_field(clean, "opening_at"),
        estimate_price_method=F.pick_field(clean, "estimate_price_method"),
        joint_method_name=F.pick_field(clean, "joint_method_name"),
        joint_method_code=F.pick_field(clean, "joint_method_code"),
        joint_agreement_deadline=F.pick_field(clean, "joint_agreement_deadline"),
        joint_member_region_limit=F.pick_field(clean, "joint_member_region_limit"),
        industry_limit_yn=F.pick_field(clean, "industry_limit_yn"),
        industry_text=F.pick_field(clean, "industry_text"),
        product_class_no=F.pick_field(clean, "product_class_no"),
        product_class_name=F.pick_field(clean, "product_class_name"),
        product_codes=_product_codes(F.pick_field(clean, "product_class_no"), F.pick_field(clean, "product_list")),
        attachments=F.extract_attachments(clean),
        raw=clean,
    )


def prespec_from_raw(raw: dict[str, Any], work_type: str) -> Notice:
    """사전규격 1건 → Notice. 본공고와 같은 자료구조로 담아 필터·자격판정을 그대로 태운다."""
    clean = F.strip_personal_fields(raw)

    def pick(key: str) -> str | None:
        return F.pick_by(clean, F.PRESPEC_FIELDS, key)

    budget_text = pick("assigned_budget")
    try:
        budget = float(budget_text.replace(",", "")) if budget_text else None
    except ValueError:
        budget = None
    linked = [n for n in re.split(r"[\s,;]+", pick("linked_bid_notices") or "") if n]
    return Notice(
        work_type=work_type,
        notice_no=pick("notice_no") or "",
        notice_ord="000",
        title=pick("title") or "(제목 없음)",
        notice_institution=pick("notice_institution"),
        demand_institution=pick("demand_institution"),
        # 사전규격 응답엔 상세 주소 칸이 없다(2026-10-12 실응답 진단) — 등록번호로 나라장터 상세 주소를 만든다.
        # 사용자 확인: 업무구분 코드(prcmBsneSeCd) 없이 등록번호만으로 열림
        detail_url=pick("detail_url") or (G2B_PRESPEC_URL.format(no=pick("notice_no")) if pick("notice_no") else None),
        assigned_budget=budget,
        posted_at=pick("posted_at"),
        product_class_no=pick("product_class_no") or next(iter(_product_codes(None, pick("product_list"))), None),
        product_codes=_product_codes(pick("product_class_no"), pick("product_list")),
        attachments=F.extract_attachments(
            clean, F.PRESPEC_ATTACHMENT_NAME_PREFIX, F.PRESPEC_ATTACHMENT_URL_PREFIX, F.PRESPEC_ATTACHMENT_MAX
        ),
        raw=clean,
        kind=PRESPEC_KIND,
        opinion_deadline=pick("opinion_deadline"),
        linked_bid_notices=linked,
    )
