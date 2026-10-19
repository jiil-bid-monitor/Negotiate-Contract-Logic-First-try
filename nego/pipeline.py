"""수집 → 수집 범위(공고 유형) → 스크리닝 → 자격 게이트 → 후보 산출.

이 파이프라인 자체는 API 응답만으로 끝난다. 첨부파일 다운로드·텍스트 추출은
`attachments.py`가 별도로 맡고, `cli.py`의 `--fetch-attachment-text` 옵션을 줬을 때만
후보에 대해 추가로 실행된다 (기본 파이프라인에는 영향 없음).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import fields as F
from . import qualify, region, scope, screen
from .config import AppConfig
from .http_client import ApiError, DataGoKrClient
from .models import Notice, notice_from_raw, prespec_from_raw

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    """수집 범위(수의계약 제외 경쟁입찰)에 든 공고 1건.

    후보로 살아남은 것과 걸러진 것을 **같은 자료구조로** 담는다.
    걸러진 공고도 마감·공동수급·자격 같은 파생값을 그대로 갖게 하려는 것이다.
    (예전에는 걸러진 공고를 별도 경로로 저장해서 파생 컬럼이 전부 비어 있었다)
    """

    notice: Notice
    screen_result: screen.ScreenResult
    qualification: qualify.QualificationResult
    joint: qualify.JointSupply
    schedule: screen.Schedule
    regions: list[str] = field(default_factory=list)
    days_left: int | None = None
    is_re_notice: bool = False
    variant: str | None = None
    # 공고 유형: 협상 / 규격가격동시입찰 / 입찰 (`scope.bid_category`)
    category: str | None = None
    is_candidate: bool = True
    excluded_reason: str | None = None
    # `--fetch-attachment-text`로 뽑은 첨부파일 원문(파일별로 이어붙임). LLM 유사도
    # 판정(`llm_similarity.py`)에 과업내용 근거로 넘긴다. 첨부를 안 뽑았으면 빈 문자열.
    attachment_text: str = ""
    # 그중 과업 문서(과업지시서·제안요청서·시방서·공내역서·연결 사전규격 첨부)의 글만 — 싱크로율·과업 근거
    task_text: str = ""
    # 첨부 공고문의 개최·사업 장소가 해외일 때 그 장소("독일 뒤셀도르프") — 표시만(2026-10-16 통합 결정 8번)
    overseas_attachment: str | None = None
    # 매일 입찰 보고에서 이미 보낸 공고의 정정공고(차수가 오름) — 보고에 [정정] (`sent_state.select_new`)
    corrected: bool = False
    # `--llm-similarity`를 줬을 때만 채워진다 (`llm_similarity.LlmJudgement`).
    llm_similarity: object | None = None
    # 지역 제한(업체 소재지) 판정 — 나라장터 참가가능지역 API 기준. "미달"이면 `drop_region_unmet`이 제외로 옮긴다(2026-10-14)
    region_check: region.RegionCheck = field(default_factory=region.RegionCheck)
    # 첨부 공고문에서 찾은 소재지 요건 — 제외에 쓰지 않고 "지역 제한 의심"으로 표시만(2026-10-19 통합 결정 7번)
    region_text: region.RegionCheck | None = None

    @property
    def region_suspect(self) -> str | None:
        """첨부 공고문 판정이 미달인데 API로는 미달이 아닐 때 — "지역 제한 의심: 경기 (첨부)"."""
        text = self.region_text
        if text is None or text.status != "미달" or self.region_check.status == "미달":
            return None
        return f"지역 제한 의심: {'·'.join(text.required)} (첨부)"
    # 첨부 공고문에서 찾은, 판정하지 않고 "확인 필요"로만 보여줄 요건 — 실적·기술인력·건축사사무소
    # (`text_requirements.flag_requirements`, [{kind, text, date?}])
    text_flags: list = field(default_factory=list)

    @property
    def gate_passed(self) -> bool:
        return self.qualification.passes

    @property
    def sort_key(self) -> tuple:
        """검토 순서. 마감 임박 → 강력추천 → 공고명(같은 순위일 때 순서를 고정하기 위함)."""
        days = self.days_left if self.days_left is not None else 9999
        confidence_rank = 0 if self.screen_result.confidence == "강력추천" else 1
        return (days, confidence_rank, self.notice.title)


@dataclass
class RunStats:
    fetched: int = 0
    failed_operations: list[str] = field(default_factory=list)
    cancelled: int = 0
    private_contract: int = 0  # 수의계약이라 뺀 공고
    design_contest: int = 0  # 설계공모라 뺀 공고 (2026-10-16 통합 결정 5번)
    other_category: int = 0  # 이번 실행 유형(--categories) 밖이라 뺀 공고
    old_ordinal: int = 0
    in_scope: int = 0  # 수집 범위(+유형)에 든 공고
    # 이번 실행에서 고른 유형. None이면 전 유형. 리포트 제목/발송 메시지에 쓴다.
    categories: list[str] | None = None
    screened_out: dict[str, int] = field(default_factory=dict)
    screened_in: int = 0
    # 자격 미달인데 후보에 남긴 공고 수 (공동수급 보완 가능성 때문에 제외하지 않음)
    qualification_flagged: int = 0
    candidates: int = 0
    license_error: str | None = None
    region_error: str | None = None
    # 수집 범위에 들었는데 후보에서 빠진 것들. 후보와 같은 Candidate 자료구조를 쓴다
    # (is_candidate=False, excluded_reason에 사유). 저장 단계에서 함께 기록된다.
    rejected: list["Candidate"] = field(default_factory=list)
    # 부가 API가 실제로 몇 건을 돌려줬는지. 0이면 "제한 없음"이 아니라 "정보 없음"이다.
    license_rows: int = 0
    region_rows: int = 0
    # 리포트 헤더의 "조회 기간" 표시용. --from-store처럼 API를 안 부른 실행에서는 None.
    period_begin: datetime | None = None
    period_end: datetime | None = None
    # 사전규격: 수집했는지, 몇 건 받았는지, 실패 사유(실패해도 본공고 결과는 그대로 낸다)
    prespec_requested: bool = False
    prespec_fetched: int = 0
    prespec_error: str | None = None


def _api_window(now: datetime, lookback_days: int) -> tuple[str, str]:
    begin = now - timedelta(days=lookback_days)
    return begin.strftime("%Y%m%d0000"), now.strftime("%Y%m%d%H%M")


def complete_days_window(now: datetime, days: int) -> tuple[datetime, datetime]:
    """오늘을 뺀 직전 `days`일을 날짜 단위로 딱 자른 구간 [시작일 00:00, 어제 23:59].

    매일/매주 정해진 주기로 돌려도 구간이 겹치거나 비지 않는다 — "이미 보낸 공고를
    다시 보내지 않는다"를 따로 발송 기록 없이 지키기 위함이다. Actions 스케줄이
    몇십 분 늦게 돌아도 구간은 그대로다.
    """
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    begin = today - timedelta(days=days)
    end = today - timedelta(minutes=1)
    return begin, end


def collect_notices(
    client: DataGoKrClient, begin: str, end: str, stats: RunStats
) -> list[Notice]:
    """업무구분 3종을 순서대로 조회한다. 하나라도 재시도를 다 소진해 실패하면

    나머지는 시도하지 않고 즉시 중단한다 — 부분 데이터로 계속 진행하지 않고,
    위(Actions 워크플로)에서 이 Run을 빨리 실패 처리해 새 Run으로 재시도할 수
    있게 하기 위함이다.
    """
    collected: list[Notice] = []

    for work_type, operation in F.BID_NOTICE_OPERATIONS.items():
        label = f"본공고/{work_type}"
        try:
            raw_items = client.fetch_all_pages_chunked(
                F.BID_NOTICE_BASE_URL,
                operation,
                {"inqryDiv": "1"},
                begin,
                end,
                label,
            )
        except ApiError as err:
            log.error("%s 조회 실패: %s", label, err)
            stats.failed_operations.append(label)
            raise

        notices = [notice_from_raw(raw, work_type) for raw in raw_items]
        log.info("%s 조회 완료: %d건", label, len(notices))
        collected.extend(notices)

    stats.fetched = len(collected)
    return collected


def collect_prespecs(client: DataGoKrClient, begin: str, end: str, stats: RunStats) -> list[Notice]:
    """사전규격 3종(용역·물품·공사)을 조회한다.

    본공고와 달리 **실패해도 멈추지 않는다** — 사전규격은 별도 서비스라 활용신청이
    안 돼 있거나 주소가 바뀌어 실패할 수 있는데, 그 때문에 본공고 수집까지 버리면 안 된다.
    실패한 업무구분은 `stats.prespec_error`에 사유를 남기고 건너뛴다.
    """
    stats.prespec_requested = True
    base_url = os.environ.get("PRESPEC_BASE_URL", "").strip() or F.PRESPEC_BASE_URL
    collected: list[Notice] = []
    errors: list[str] = []
    for work_type, operation in F.PRESPEC_OPERATIONS.items():
        label = f"사전규격/{work_type}"
        try:
            raw_items = client.fetch_all_pages_chunked(base_url, operation, {"inqryDiv": "1"}, begin, end, label)
        except ApiError as err:
            log.warning("%s 조회 실패 — 사전규격은 건너뛰고 본공고만 진행합니다: %s", label, err)
            errors.append(f"{label}: {err}")
            continue
        notices = [prespec_from_raw(raw, work_type) for raw in raw_items]
        notices = [n for n in notices if n.notice_no]
        log.info("%s 조회 완료: %d건", label, len(notices))
        collected.extend(notices)
    stats.prespec_fetched = len(collected)
    stats.prespec_error = " / ".join(errors) or None
    _log_attachmentless_prespecs(collected)
    return collected


def _log_attachmentless_prespecs(notices: list[Notice]) -> None:
    """첨부 없는 사전규격(국방전자조달 연계 등) 진단 로그 — 어느 응답 칸이 연계기관을 알려주는지 실응답으로
    확인하려고 남긴다(2026-10-12). 앞 3건만, 값은 40자까지. 담당자 개인정보 칸은 이미 빠져 있다."""
    empty = [n for n in notices if not n.attachments]
    if not empty:
        return
    linked = sum(1 for n in empty if n.linked_system)
    log.info("첨부 없는 사전규격 %d건 (국방전자조달 등 연계기관으로 판별 %d건)", len(empty), linked)
    for n in empty[:3]:
        fields = ", ".join(f"{k}={str(v)[:40]}" for k, v in n.raw.items() if v not in (None, ""))
        log.info("  [진단] %s %s — %s", n.notice_no, n.title[:30], fields)


def build_candidates(
    notices: list[Notice],
    config: AppConfig,
    license_groups: dict[str, list[qualify.LicenseGroup]],
    regions: dict[str, list[str]],
    now: datetime,
    stats: RunStats,
    categories: set[str] | None = None,
) -> list[Candidate]:
    scope_result = scope.apply_scope(notices, categories)
    stats.cancelled = scope_result.dropped_cancelled
    stats.private_contract = scope_result.dropped_private
    stats.design_contest = scope_result.dropped_design_contest
    stats.other_category = scope_result.dropped_other_category
    stats.old_ordinal = scope_result.dropped_old_ordinal
    stats.in_scope = len(scope_result.kept)
    stats.categories = sorted(categories, key=scope.CATEGORIES.index) if categories else None

    candidates: list[Candidate] = []
    company_location = region.company_location(config.held_raw)
    held_codes = qualify.load_held_codes(config.held_raw)

    for notice in scope_result.kept:
        # 파생값은 후보/제외 가릴 것 없이 **모든 협상 공고에 대해** 먼저 계산한다.
        # 전부 로컬 계산이라 비용이 없고, 나중에 "왜 걸러졌지?"를 볼 때 이 값들이 필요하다.
        schedule = screen.build_schedule(notice)
        screen_result = screen.screen(notice, config.screen)
        qualification = qualify.evaluate(license_groups.get(notice.notice_no, []), config.held_names, held_codes)

        record = Candidate(
            notice=notice,
            screen_result=screen_result,
            qualification=qualification,
            joint=qualify.parse_joint_supply(notice.joint_method_name),
            schedule=schedule,
            regions=regions.get(notice.notice_no, []),
            days_left=schedule.days_left(now),
            is_re_notice=scope.is_re_notice(notice),
            variant=scope.negotiation_variant(notice),
            category=scope.bid_category(notice),
            region_check=region.from_api(regions.get(notice.notice_no, []), company_location),
        )

        # 이미 본공고로 나간 사전규격은 후보에서 뺀다 (사용자 요청 2026-09-30) — 같은 사업이
        # 본공고로 다시 보이고, 사전규격 쪽은 의견등록 마감도 대개 지났다. 제외 목록에는 남긴다.
        if notice.kind == scope.CATEGORY_PRESPEC and notice.linked_bid_notices:
            reason = "본공고 게시됨"
            stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
            record.is_candidate = False
            record.excluded_reason = f"{reason} ({', '.join(notice.linked_bid_notices)})"
            stats.rejected.append(record)
            continue

        if not screen_result.matched:
            reason = screen_result.excluded_by or "미매칭"
            detail = screen_result.excluded_reason or ""
            stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
            record.is_candidate = False
            record.excluded_reason = f"{reason}{f' ({detail})' if detail else ''}"
            stats.rejected.append(record)
            continue

        # 요구 면허를 지일이 하나도 갖고 있지 않은 공고는 뺀다(2026-10-13 사용자 결정) — 지일이 맡을 부분이 없어
        # 공동수급으로도 못 들어간다. 조경·토목공사업만 요구하는 공고(대리님 답변)도 여기서 빠진다. 하나라도 보유하면
        # 남겨서 자격 미달·공동수급 칸으로 담당자가 판단. 면허제한 API 기준이라 사전규격·첨부에만 적힌 자격엔 해당 없음.
        required = qualify.no_held_license(license_groups.get(notice.notice_no, []), config.held_names, held_codes)
        if required:
            reason = "보유면허없음"
            shown = ", ".join(required[:4]) + (f" 외 {len(required) - 4}개" if len(required) > 4 else "")
            stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
            record.is_candidate = False
            record.excluded_reason = f"{reason} (요구 면허 {shown} — 지일 보유 없음, 공동수급 불가)"
            stats.rejected.append(record)
            continue

        stats.screened_in += 1

        # 자격 미달은 후보에서 빼지 않는다 (2026-09-28 사용자 결정: 미보유 자격 개수 무시).
        # 공동수급으로 보완해 수주하는 경우가 있어, 리포트의 자격판정(빨간 원)과
        # 공동수급 칸을 보고 담당자가 판단한다. 예전엔 미보유 그룹 2개 이상이면 제외했다.
        if qualification.checked and qualification.missing_count:
            stats.qualification_flagged += 1

        candidates.append(record)

    candidates.sort(key=lambda c: c.sort_key)
    stats.candidates = len(candidates)
    return candidates


def drop_expired(candidates: list[Candidate], now: datetime, stats: RunStats) -> list[Candidate]:
    """기준 마감(자격등록·입찰·의견등록 중 가장 이른 것, `Schedule.earliest`)이 지난 후보를 제외 목록으로 옮긴다
    (2026-10-19 통합 결정 4번 — 마감 지난 공고는 화면·보고 어디에도 안 보임). 첨부 공고문의 제출기한으로 마감을
    채우는 공고가 있어 첨부를 읽은 **뒤에** 부른다. 마감 정보가 없으면 남긴다."""
    kept = []
    for c in candidates:
        if not c.schedule.is_expired(now):
            kept.append(c)
            continue
        label, dt = c.schedule.earliest
        reason = "마감 지남"
        stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
        stats.screened_in -= 1
        c.is_candidate = False
        c.excluded_reason = f"{reason} ({label} {dt:%m/%d %H:%M})"
        stats.rejected.append(c)
    stats.candidates = len(kept)
    return kept


def drop_region_unmet(candidates: list[Candidate], stats: RunStats) -> list[Candidate]:
    """지역 제한을 못 채운 후보를 제외 목록으로 옮긴다(2026-10-14 사용자 결정 — 9/28 "지역제한은 표시만"을 바꿈).

    지역제한 입찰은 공동수급을 허용해도 구성원 모두가 소재지 요건을 채워야 해서 지일은 들어갈 수 없다.
    **나라장터 참가가능지역 API 판정만** 본다(2026-10-19 통합 결정 7번) — 첨부 공고문 판정은 `region_suspect` 표시만.
    시·군 단위까지 본다(강원 춘천시만 → 정선군 지일은 미달). "미확인"은 남긴다.
    """
    kept = []
    for c in candidates:
        check = c.region_check
        if check.status != "미달":
            kept.append(c)
            continue
        reason = "지역제한"
        required = "·".join(check.required) or "다른 지역"
        stats.screened_out[reason] = stats.screened_out.get(reason, 0) + 1
        stats.screened_in -= 1
        c.is_candidate = False
        c.excluded_reason = f"{reason} ({required}만 참가 — 지일 {check.company}, 근거: {check.source})"
        stats.rejected.append(c)
    stats.candidates = len(kept)
    return kept


def run(
    config: AppConfig,
    now: datetime | None = None,
    categories: set[str] | None = None,
    complete_days: int | None = None,
    include_prespec: bool = False,
    period: tuple[datetime, datetime] | None = None,
) -> tuple[list[Candidate], RunStats, list[Notice]]:
    """`complete_days`를 주면 조회 기간을 오늘 뺀 직전 N일(날짜 단위)로 잡는다
    (매일/매주 발송용). `period`(시작, 끝)를 주면 그 구간(웹앱 "직접 지정"). 둘 다 없으면 기존처럼
    최근 `lookback_days`일 ~ 지금. `include_prespec`이면 같은 기간의 사전규격도 받아 본공고와 같은 필터·판정을 태운다."""
    now = now or datetime.now()
    stats = RunStats()
    client = DataGoKrClient(config.api)

    if period:
        period_begin, period_end = period
        begin, end = period_begin.strftime("%Y%m%d%H%M"), period_end.strftime("%Y%m%d%H%M")
        stats.period_begin, stats.period_end = period_begin, period_end
    elif complete_days:
        period_begin, period_end = complete_days_window(now, complete_days)
        begin, end = period_begin.strftime("%Y%m%d%H%M"), period_end.strftime("%Y%m%d%H%M")
        stats.period_begin, stats.period_end = period_begin, period_end
    else:
        begin, end = _api_window(now, config.lookback_days)
        stats.period_begin = now - timedelta(days=config.lookback_days)
        stats.period_end = now
    log.info("조회 기간: %s ~ %s", begin, end)

    # collect_notices()가 세 부문 중 하나라도 실패하면 바로 ApiError를 올린다
    # (부분 데이터로 계속 진행하지 않는다). 여기서 따로 잡지 않고 그대로
    # cli.py까지 전파해 종료코드 1로 끝나게 둔다 — Actions 워크플로가 그걸 보고
    # 새 Run으로 재시도한다.
    notices = collect_notices(client, begin, end, stats)
    if include_prespec:
        notices = notices + collect_prespecs(client, begin, end, stats)

    license_groups, license_error = qualify.fetch_license_groups(client, begin, end)
    stats.license_error = license_error
    stats.license_rows = len(license_groups)
    if license_error:
        log.warning("면허제한정보 조회 실패 — 자격 게이트를 건너뜁니다 (fail-open): %s", license_error)
    else:
        log.info("면허제한정보: 공고 %d건분 수신", len(license_groups))

    region_map, region_error = qualify.fetch_region_limits(client, begin, end)
    stats.region_error = region_error
    stats.region_rows = len(region_map)
    if region_error:
        log.warning("참가가능지역 조회 실패: %s", region_error)
    else:
        log.info("참가가능지역: 공고 %d건분 수신", len(region_map))

    candidates = build_candidates(notices, config, license_groups, region_map, now, stats, categories)
    return candidates, stats, notices
