"""「나라장터에서 불러오기」 — `nego` 수집·자격판정을 그대로 돌리고 화면용 JSON으로 바꾼다.

수집 로직은 CLI(`python -m nego --fetch-attachment-text`)와 같은 함수를 같은 순서로 부른다:
`pipeline.run` → (선택) `attachments.save_attachment_texts`(첨부 참가자격을 API 판정에
겹치고 일정 미상을 보충). 여기서 새로 더하는 것은 싱크로율·AI 판단 표시뿐이다.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from nego import qualify, scope
from nego.business_scope import extract_scope_items
from nego.config import AppConfig, load_config, redact

from . import qualview
from .store import Store
from .sync import PastIndex

log = logging.getLogger(__name__)

AI_LABEL = {"유사": "적합", "부분 유사": "검토필요", "무관": "부적합"}
PERIODS = [1, 3, 7, 14, 30]
MAX_CUSTOM_DAYS = 92  # 직접 지정 구간 상한 — 나라장터 호출량 보호 (긴 구간은 30일 단위로 나눠 조회한다)


def custom_period(begin: str, end: str, now: datetime) -> tuple[datetime, datetime]:
    """"YYYY-MM-DD" 두 개 → [시작일 00:00, 종료일 23:59] (종료일이 오늘이면 지금까지)."""
    try:
        b = datetime.strptime(begin, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError("기간은 YYYY-MM-DD 형식의 시작일·종료일로 지정하세요") from None
    if b > e:
        raise ValueError("시작일이 종료일보다 늦습니다")
    if b.date() > now.date():
        raise ValueError("시작일이 오늘보다 늦습니다")
    if (e - b).days + 1 > MAX_CUSTOM_DAYS:
        raise ValueError(f"직접 지정 기간은 {MAX_CUSTOM_DAYS}일까지입니다")
    e_end = e.replace(hour=23, minute=59)
    return b, min(e_end, now)
# 저장되는 수집 결과의 판정·표시 형식 버전. 자격 판정·팝업 자료가 바뀔 때 올린다 — 화면이 예전 버전으로
# 수집된 결과를 보여주고 있으면 "다시 불러오기" 안내를 띄운다(화면은 서버가 알려 주는 이 값과 결과의 format을 비교 — 2026-09-30: 코드를 받고도 옛 결과를 보고
# 중복·개수 오류가 그대로라고 여긴 일이 두 번 있었음).
RESULT_FORMAT = 40


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def _g2b_url(n) -> str | None:
    """API가 상세 URL을 안 줄 때 쓰는 나라장터 공고 상세 주소 (bidNtceDtlUrl과 같은 형식)."""
    if n.kind != "본공고" or not n.notice_no:
        return None
    return f"https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={n.notice_no}&bidPbancOrd={n.notice_ord}"


def serialize(candidate, past: PastIndex, held: tuple[set[str], set[str]] = (set(), set()),
              interest: Callable[[Any], list[str]] | None = None) -> dict[str, Any]:
    """Candidate 1건 → 카드 1장. 후보/제외 공고 모두 같은 모양.

    `interest`: 공고명에 든 관심 키워드를 찾는 함수 — 제외된 공고는 선별에서 키워드를 안 세서(제외 키워드에서 멈춤)
    화면 "제외 사유" 칸의 "공고명에 관심 키워드 …" 줄을 따로 채운다(2026-10-19 팀원 화면 이식)."""
    n = candidate.notice
    q = candidate.qualification
    earliest = candidate.schedule.earliest
    # 싱크로율 근거는 과업 문서 글 우선(과업지시서·제안요청서·공내역서 — 2026-10-19 통합), 없으면 첨부 전체
    sync = past.score(n.title, getattr(candidate, "task_text", "") or candidate.attachment_text)
    ai = candidate.llm_similarity
    ai_row = None
    if ai is not None:
        best = ai.best
        ai_row = {
            "label": AI_LABEL.get(ai.overall, "미판정"),
            "overall": ai.overall,
            "reason": ai.error or ai.overall_reason,
            "best": getattr(best, "project_title", None) if best else None,
        }
    has_text = bool(candidate.attachment_text.strip())
    # 자격요건은 본공고면 면허제한정보 API를 봤고, 세부품명번호는 첨부 원문을 읽었을 때만 볼 수 있다
    parts = qualview.build(q, held, {"industry": n.kind == "본공고" or has_text, "product": has_text})
    missing = qualview.flat_missing(parts)
    return {
        "key": f"{n.notice_no}-{n.notice_ord}",
        "notice_no": n.notice_no,
        "notice_ord": n.notice_ord,
        "kind": n.kind,
        "linked_bid_notices": n.linked_bid_notices,
        "title": n.title,
        "work_type": n.work_type,
        "category": candidate.category,
        "award_method": n.award_method,
        "confidence": candidate.screen_result.confidence,
        "matched_keywords": candidate.screen_result.matched_keywords,
        "matched_product_codes": candidate.screen_result.matched_product_codes,
        # 공고에 분류된 세부품명 전부 — 화면 "세부품명" 필터·"세부품명 공고" 탭(제외된 공고 중 보유 품목)에 쓴다
        "product_codes": list(getattr(n, "product_codes", None) or ([n.product_class_no] if n.product_class_no else [])),
        "product_name": n.product_class_name or None,
        "interest_keywords": (candidate.screen_result.matched_keywords or (interest(n) if interest else [])),
        # 관심 / 세부품명 (키워드 없이 등록 품목만 맞은 공고) — 2026-10-19 통합 결정 10·12번
        "section": candidate.screen_result.section,
        "overseas_suspect": candidate.screen_result.overseas_suspect,
        "overseas_attachment": getattr(candidate, "overseas_attachment", None),
        # 몽골 해외관(제외하지 않고 표시) 근거
        "overseas_mongolia": candidate.screen_result.overseas_evidence if candidate.screen_result.overseas_flag else None,
        "review_exclude": candidate.screen_result.review_exclude,
        "is_candidate": candidate.is_candidate,
        "excluded_reason": candidate.excluded_reason,
        "match_explain": candidate.screen_result.match_explain if not candidate.is_candidate else [],
        "match_detail": candidate.screen_result.match_detail if not candidate.is_candidate else {},
        "is_re_notice": candidate.is_re_notice,
        "demand_institution": n.demand_institution or n.notice_institution,
        "notice_institution": n.notice_institution,
        "budget": n.budget,
        # 추정가격 / 배정예산 / None(금액 모름) — 2026-10-19 통합 결정 2번
        "budget_kind": n.budget_kind,
        "posted_at": n.posted_at,
        "deadline_label": earliest[0] if earliest else None,
        "deadline": _iso(earliest[1]) if earliest else None,
        "deadlines": [[label, _iso(dt)] for label, dt in candidate.schedule.all_deadlines],
        "days_left": candidate.days_left,
        "detail_url": n.detail_url or _g2b_url(n),
        "regions": candidate.regions,
        "region_check": candidate.region_check.to_dict(),
        # 첨부 공고문의 소재지 요건 — 제외 안 하고 표시만(2026-10-19 통합 결정 7번)
        "region_text": candidate.region_text.to_dict() if getattr(candidate, "region_text", None) else None,
        "region_suspect": getattr(candidate, "region_suspect", None),
        "text_flags": getattr(candidate, "text_flags", []),
        "joint": {"label": candidate.joint.label, "allowed": candidate.joint.allowed},
        "qualification": {
            "checked": q.checked,
            "passes": q.passes,
            "summary": q.summary,
            "missing": missing,
            "held": [label for p in parts for label in p["held"]],
            "parts": parts,
        },
        "has_attachment_text": has_text,
        "attachment_count": len(n.attachments),
        # 규격서가 다른 조달시스템(국방전자조달)에 있는 공고 — 카드에 "국방전자조달에서 확인" 칩
        "linked_system": n.linked_system,
        # 첨부의 "사업 범위" 목록 — 검토 필요 카드의 과업 유사 팝업에 보여준다(없으면 빈 목록)
        "scope_items": extract_scope_items(candidate.attachment_text)[:10] if has_text else [],
        "sync": sync,
        "ai": ai_row,
    }


def _judge_ai(candidates, past: PastIndex, max_candidates: int) -> int:
    """AI 판단 — `llm_similarity.judge_notice`에 싱크로율 상위 과거 실적 8건만 넘긴다
    (150건 전부를 매번 보내지 않으려고). ANTHROPIC_API_KEY 없으면 건너뛴다."""
    from nego import llm_similarity
    from nego.similarity import PastProject

    config = llm_similarity.LlmConfig.from_env()
    if config is None:
        log.warning("ANTHROPIC_API_KEY 없음 → AI 판단 건너뜀")
        return 0
    client = llm_similarity.make_client()
    done = 0
    for candidate in candidates[:max_candidates]:
        sync = past.score(candidate.notice.title, candidate.attachment_text, top=8)
        projects = [
            PastProject(
                title=f"({row['year']}) {row['title']}",
                summary_text=f"과업: {past.projects[row['index']].get('overview', '')}\n"
                f"전시내용: {past.projects[row['index']].get('exhibition', '')}",
            )
            for row in sync["top"]
        ]
        if not projects:
            continue
        judgement = llm_similarity.judge_notice(
            client, config, llm_similarity.NoticeInput.from_candidate(candidate), projects
        )
        candidate.llm_similarity = judgement
        done += 1
        log.info("AI 판단 %d/%d: %s → %s", done, min(len(candidates), max_candidates),
                 candidate.notice.title[:30], judgement.overall)
    return done


@dataclass
class Job:
    running: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    error: str | None = None
    run_id: int | None = None  # 저장된 결과 번호 — 수집을 누른 사람 화면만 이 결과로 바뀐다

    def snapshot(self) -> dict[str, Any]:
        return {"running": self.running, "started_at": self.started_at, "finished_at": self.finished_at,
                "params": self.params, "log": self.log[-30:], "error": self.error, "run_id": self.run_id}


class _JobLogHandler(logging.Handler):
    def __init__(self, job: Job):
        super().__init__(logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        self.job.log.append(f"{datetime.now():%H:%M:%S} {record.getMessage()}")


class Collector:
    """수집은 한 번에 하나만 돈다(나라장터 API 호출량 보호). 결과는 Store에 남겨 팀원이 같이 본다."""

    def __init__(self, store: Store, past: PastIndex,
                 runner: Callable[..., tuple[list, Any]] | None = None,
                 config_loader: Callable[[], AppConfig] = load_config):
        self.store = store
        self.past = past
        self.job = Job()
        self._lock = threading.Lock()
        self._runner = runner or self._run_nego
        self._config_loader = config_loader

    def start(self, days: int | None, attachments: bool, ai: bool, categories: list[str] | None,
              prespec: bool = True, begin: str | None = None, end: str | None = None) -> Job:
        """`begin`·`end`("YYYY-MM-DD")를 주면 그 구간, 아니면 최근 `days`일."""
        period = None
        if begin or end:
            period = custom_period(begin or "", end or "", datetime.now())
            days = None
        elif days not in PERIODS:
            raise ValueError(f"기간은 {PERIODS}일 중 하나")
        cats = scope.parse_categories(",".join(categories)) if categories else None
        with self._lock:
            if self.job.running:
                raise RuntimeError("이미 수집 중입니다")
            self.job = Job(running=True, started_at=datetime.now().isoformat(timespec="seconds"),
                           params={"days": days, "begin": begin if period else None, "end": end if period else None,
                                   "attachments": attachments, "ai": ai, "prespec": prespec,
                                   "categories": sorted(cats) if cats else None})
        threading.Thread(target=self._work, args=(self.job, days, attachments, ai, cats, prespec, period),
                         daemon=True).start()
        return self.job

    def _run_nego(self, config: AppConfig, days: int | None, attachments: bool, cats, prespec: bool = True,
                  period: tuple[datetime, datetime] | None = None):
        from nego.pipeline import drop_expired, drop_region_unmet, run

        if days:
            config.lookback_days = days
        # 관심·제외 키워드가 함께 있는 공고는 빼지 않고 "검토 필요"로 — 웹앱에서만 (CLI·자동 발송은 예전처럼 제외)
        config.screen.review_conflicts = True
        now = datetime.now()
        candidates, stats, _ = run(config, now, cats, include_prespec=prespec, period=period)
        if attachments:
            from nego.attachments import save_attachment_texts
            from nego.http_client import DataGoKrClient

            att = save_attachment_texts(
                candidates, config.output_dir,
                held_codes=qualify.load_held_codes(config.held_raw),
                held_code_names=qualify.load_held_code_names(config.held_raw),
                now=now, code_names=config.code_names, held_raw=config.held_raw,
                client=DataGoKrClient(config.api) if config.api.service_key else None,
            )
            log.info("첨부파일: 시도 %d → 성공 %d / 실패 %d · 자격판정 반영 %d건",
                     att["attempted"], att["ok"], att["failed"], att["qualification_determined"])
            if att["unnamed_codes"]:
                log.info("이름 미확인 코드 (config/code_names.json에 추가): %s", ", ".join(att["unnamed_codes"]))
        # 지역 제한 미달은 제외(2026-10-14) — 첨부 공고문 판정이 API보다 우선이라 첨부를 읽은 뒤에
        candidates = drop_expired(candidates, datetime.now(), stats)
        candidates = drop_region_unmet(candidates, stats)
        return candidates, stats

    def _work(self, job: Job, days: int | None, attachments: bool, ai: bool, cats, prespec: bool = True,
              period: tuple[datetime, datetime] | None = None) -> None:
        handler = _JobLogHandler(job)
        root = logging.getLogger()
        root.addHandler(handler)
        if root.level > logging.INFO:
            root.setLevel(logging.INFO)
        config = None
        try:
            config = self._config_loader()
            if not config.api.service_key:
                raise RuntimeError("NARA_SERVICE_KEY 환경변수가 없습니다 (서버를 띄운 창에 설정)")
            span = (f"{period[0]:%Y-%m-%d} ~ {period[1]:%Y-%m-%d}" if period else f"최근 {days}일")
            log.info("수집 시작: %s%s%s", span, " + 사전규격" if prespec else "",
                     " + 첨부 자격판정" if attachments else "")
            candidates, stats = (self._runner(config, days, attachments, cats, prespec, period) if period
                                 else self._runner(config, days, attachments, cats, prespec))
            if ai:
                import os
                _judge_ai(candidates, self.past, int(os.environ.get("LLM_MAX_CANDIDATES", "30")))
            held = qualview.held_lookup(config.held_raw)
            from nego.screen import _match_keywords

            def interest(n, sc=config.screen):
                return _match_keywords(n, list(sc.keywords), sc.keyword_skip_when)
            payload = {
                "format": RESULT_FORMAT,
                # 미매칭 설명에 쓰는 관심 키워드 목록 — 공고마다 같아서 결과에 한 번만
                "keywords": list(config.screen.keywords),
                "candidates": [serialize(c, self.past, held, interest) for c in candidates],
                "rejected": [serialize(c, self.past, held, interest) for c in stats.rejected],
                "stats": {
                    "fetched": stats.fetched, "in_scope": stats.in_scope,
                    "private_contract": stats.private_contract, "cancelled": stats.cancelled,
                    "screened_out": stats.screened_out, "candidates": stats.candidates,
                    "qualification_flagged": stats.qualification_flagged,
                    "license_error": stats.license_error, "region_error": stats.region_error,
                    "prespec_requested": stats.prespec_requested, "prespec_fetched": stats.prespec_fetched,
                    "prespec_error": stats.prespec_error,
                    "period_begin": _iso(stats.period_begin), "period_end": _iso(stats.period_end),
                },
            }
            job.run_id = self.store.save_run(job.started_at, job.params, payload)
            log.info("수집 완료: 후보 %d건 · 제외 %d건", len(payload["candidates"]), len(payload["rejected"]))
        except Exception as err:  # 화면에 사유를 그대로 보여준다 (서비스키는 가림)
            key = config.api.service_key if config else ""
            job.error = redact(f"{type(err).__name__}: {err}", [key] if key else [])
            log.error("수집 실패: %s", job.error)
        finally:
            job.running = False
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            root.removeHandler(handler)
