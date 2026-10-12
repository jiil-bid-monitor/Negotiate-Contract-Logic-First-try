"""실행 진입점.

    python -m nego                    # 최근 LOOKBACK_DAYS일치 수집 → 리포트
    python -m nego --days 7           # 기간 지정
    python -m nego --from-store       # API 호출 없이 저장된 원문으로 재필터링
    python -m nego --verify           # 응답 필드명 진단 (필드가 비어 보일 때)
    python -m nego --fetch-attachment-text  # 후보 공고 첨부파일 텍스트 추출(원문 대조용)
    python -m nego --fetch-attachment-text --llm-similarity  # + 과거 실적과 LLM 유사도 판정
    python -m nego --categories 입찰 --complete-days 1   # 어제 게시된 입찰 공고만
    python -m nego --categories 협상,규격가격동시입찰 --complete-days 7  # 지난 7일
    python -m nego --categories 입찰 --complete-days 1 --telegram   # + 결과를 텔레그램으로 발송
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from . import fields as F
from . import qualify, scope
from .config import ConfigError, load_config, redact
from .http_client import ApiError, DataGoKrClient
from .pipeline import build_candidates, run
from .report import render_console, save_reports
from .repository import JsonlRepository


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _verify_api(config) -> int:
    """각 오퍼레이션을 소량 호출해 실제 응답 키를 그대로 출력한다."""
    client = DataGoKrClient(config.api)
    client.config.num_of_rows = 5
    client.config.max_pages = 1
    now = datetime.now()
    begin = now.replace(day=1).strftime("%Y%m%d0000")
    end = now.strftime("%Y%m%d%H%M")

    targets = [(f"본공고/{w}", F.BID_NOTICE_BASE_URL, op) for w, op in F.BID_NOTICE_OPERATIONS.items()]
    targets.append(("면허제한정보", F.BID_NOTICE_BASE_URL, F.LICENSE_LIMIT_OPERATION))
    targets.append(("참가가능지역", F.BID_NOTICE_BASE_URL, F.REGION_LIMIT_OPERATION))
    prespec_url = os.environ.get("PRESPEC_BASE_URL", "").strip() or F.PRESPEC_BASE_URL
    targets += [(f"사전규격/{w}", prespec_url, op) for w, op in F.PRESPEC_OPERATIONS.items()]

    for label, base_url, operation in targets:
        print(f"\n── {label} ({operation}) " + "─" * 30)
        try:
            items = client.fetch_all_pages(
                base_url, operation, {"inqryDiv": "1", "inqryBgnDt": begin, "inqryEndDt": end}, label
            )
        except ApiError as err:
            print(f"  실패: {redact(str(err), [config.api.service_key])}")
            continue
        if not items:
            print("  응답 0건")
            continue
        print(f"  {len(items)}건 수신. 첫 건의 필드 키 (담당자 개인정보 필드는 뺌):")
        first = F.strip_personal_fields(items[0])
        for key in sorted(first.keys()):
            value = str(first[key])[:40]
            print(f"    {key:32s} = {value}")
    return 0


def _save_to_supabase(candidates, stats) -> None:
    """Supabase 환경변수가 있으면 저장한다. 없으면 조용히 건너뛴다.

    저장 실패가 전체 실행을 죽이지 않게 한다 — 리포트는 이미 만들어졌고,
    다음 실행에서 같은 공고를 upsert하면 복구되기 때문이다.
    """
    from .supabase_repo import SupabaseConfig, SupabaseError, SupabaseRepository

    config = SupabaseConfig.from_env()
    if config is None:
        logging.info("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 없음 → Supabase 저장 건너뜀")
        return

    try:
        saved = SupabaseRepository(config).save(candidates, stats.rejected)
        print(f"Supabase 저장: {saved}행 → \"{config.table}\" (후보 {len(candidates)} / 제외 {len(stats.rejected)})")
    except SupabaseError as err:
        logging.error("Supabase 저장 실패 (리포트는 정상 생성됨): %s", err)


def _run_llm_similarity(candidates, config, now) -> dict[str, Path]:
    """LLM 유사도 판정. 자격증명이 없거나 실패해도 수집 결과(리포트/저장)는 그대로 낸다."""
    from . import llm_similarity
    from .config import DEFAULT_CONFIG_DIR
    from .similarity import load_past_projects_file

    llm_config = llm_similarity.LlmConfig.from_env()
    if llm_config is None:
        logging.warning("ANTHROPIC_API_KEY 없음 → LLM 유사도 판정 건너뜀")
        return {}

    projects = load_past_projects_file(DEFAULT_CONFIG_DIR / "past_projects.json")
    results = llm_similarity.judge_candidates(candidates, projects, llm_config)
    if not results:
        return {}
    print(llm_similarity.render_console(results))
    return llm_similarity.save_results(results, config.output_dir, now)


def _send_telegram(candidates, stats, html_path) -> None:
    """발송 실패는 종료코드에 반영하지 않는다 — 실패로 끝내면 워크플로 자동 재시도가
    수집부터 다시 돌려 이미 받은 대화방에 같은 보고서가 또 간다. 대신 Actions 화면에
    오류 표시(::error::)를 남긴다."""
    from .telegram import TelegramConfig, migration_notes, send_report

    tg_config = TelegramConfig.from_env()
    if tg_config is None:
        logging.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 없음 → 텔레그램 발송 건너뜀")
        return
    errors = send_report(tg_config, candidates, stats, html_path)
    sent = len(tg_config.chat_ids) - len(errors)
    print(f"텔레그램 발송: 대화방 {sent}/{len(tg_config.chat_ids)}곳 성공")
    for err in errors:
        logging.error("텔레그램 발송 실패 %s", err)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error::텔레그램 발송 실패 {err}")
    for note in migration_notes(tg_config, "TELEGRAM_CHAT_ID"):
        logging.warning(note)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning::{note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nego", description="나라장터 「협상에 의한 계약」 공고 추출")
    parser.add_argument("--days", type=int, help="조회 기간(일). 기본값은 LOOKBACK_DAYS 환경변수")
    parser.add_argument("--from-store", action="store_true", help="API 호출 없이 저장된 원문으로 재필터링")
    parser.add_argument("--verify", action="store_true", help="응답 필드명 진단")
    parser.add_argument("--store", default="data/notices.jsonl", help="원문 저장 경로 (로컬 JSONL)")
    parser.add_argument("--output", help="리포트 출력 폴더 (기본 output/)")
    parser.add_argument(
        "--no-supabase",
        action="store_true",
        help="Supabase 저장을 건너뛴다 (환경변수가 있어도)",
    )
    parser.add_argument(
        "--fetch-attachment-text",
        action="store_true",
        help="후보 공고의 첨부파일(HWP/HWPX/PDF)을 내려받아 텍스트를 추출한다 (지역제한/면허제한/공동수급 원문 대조용)",
    )
    parser.add_argument(
        "--llm-similarity",
        action="store_true",
        help="후보 공고를 과거 실적(config/past_projects.json)과 Claude API로 유사도 판정한다 "
        "(ANTHROPIC_API_KEY 필요, --fetch-attachment-text와 같이 주면 첨부 원문까지 근거로 씀)",
    )
    parser.add_argument(
        "--categories",
        help="이번 실행에서 다룰 공고 유형 (쉼표 구분: 협상, 규격가격동시입찰, 입찰). 비우면 전 유형",
    )
    parser.add_argument(
        "--complete-days",
        type=int,
        help="조회 기간을 오늘을 뺀 직전 N일로 날짜 단위로 자른다 (매일=1, 주간=7). "
        "정해진 주기로 돌릴 때 기간이 겹치지 않아 같은 공고를 두 번 보내지 않는다",
    )
    parser.add_argument(
        "--pre-spec",
        action="store_true",
        help="사전규격도 같은 기간으로 수집해 같은 필터·자격판정을 적용한다 "
        "(공공데이터포털에서 '사전규격정보서비스' 활용신청 필요, 실패해도 본공고는 계속)",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="결과를 텔레그램으로 발송한다 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 필요)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    try:
        config = load_config()
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 1

    try:
        categories = scope.parse_categories(args.categories)
    except ValueError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 1

    if args.days:
        config.lookback_days = args.days
    if args.output:
        config.output_dir = Path(args.output)

    if args.verify:
        if not config.api.service_key:
            print("NARA_SERVICE_KEY 환경변수가 필요합니다.", file=sys.stderr)
            return 1
        return _verify_api(config)

    now = datetime.now()
    repo = JsonlRepository(Path(args.store))

    try:
        if args.from_store:
            notices = repo.all()
            if not notices:
                print(f"저장된 원문이 없습니다: {args.store}", file=sys.stderr)
                return 1
            logging.info("저장소에서 %d건을 읽었습니다 (API 호출 없음)", len(notices))
            from .pipeline import RunStats

            stats = RunStats(fetched=len(notices))
            candidates = build_candidates(notices, config, {}, {}, now, stats, categories)
        else:
            if not config.api.service_key:
                print("NARA_SERVICE_KEY 환경변수가 필요합니다.", file=sys.stderr)
                return 1
            candidates, stats, notices = run(config, now, categories, args.complete_days, args.pre_spec)
            added = repo.upsert(notices)
            logging.info("저장 완료: 신규 %d건 / 전체 %d건", added, len(notices))
    except ApiError as err:
        print(redact(str(err), [config.api.service_key]), file=sys.stderr)
        return 1

    if args.fetch_attachment_text:
        # 리포트/Supabase 저장보다 먼저 실행한다 — 여기서 갱신되는
        # candidate.qualification(첨부파일 자격요건을 API 판정에 겹친 결과)과
        # candidate.schedule.attachment_deadline(일정 미상 공고를 첨부파일로 보충한
        # 결과)이 리포트와 Supabase 저장 내용에 반영되어야 하기 때문이다.
        from .attachments import save_attachment_texts

        held_codes = qualify.load_held_codes(config.held_raw)
        held_code_names = qualify.load_held_code_names(config.held_raw)
        att_stats = save_attachment_texts(
            candidates,
            config.output_dir,
            held_codes=held_codes,
            held_code_names=held_code_names,
            now=now,
            code_names=config.code_names,
            held_raw=config.held_raw,
        )
        print(
            f"첨부파일 텍스트 추출: 시도 {att_stats['attempted']}건 "
            f"→ 성공 {att_stats['ok']} / 실패 {att_stats['failed']}"
            f" · 참가자격 절 발견 {att_stats['qualification_found']}건"
            f" · 첨부파일 자격요건 판정에 반영 {att_stats['qualification_determined']}건"
            f" · 일정 미상 → 첨부파일로 보충 {att_stats['deadline_determined']}건"
            f" (저장 위치: {config.output_dir / 'attachment_text'})"
        )
        if att_stats["unnamed_codes"]:
            # 리포트에 "이름 미확인(코드)"로 뜨는 미보유 자격. 이름을 확인해
            # config/code_names.json에 추가하면 다음 실행부터 이름이 붙는다.
            print(
                "이름 미확인 코드 (config/code_names.json에 추가하세요): "
                + ", ".join(att_stats["unnamed_codes"])
            )

    llm_paths: dict[str, Path] = {}
    if args.llm_similarity:
        llm_paths = _run_llm_similarity(candidates, config, now)

    print(render_console(candidates, stats))

    if not args.no_supabase:
        _save_to_supabase(candidates, stats)

    paths = save_reports(candidates, stats, config.output_dir, now)
    for kind, path in {**paths, **llm_paths}.items():
        print(f"{kind.upper()} 저장: {path}")

    if args.telegram:
        _send_telegram(candidates, stats, paths.get("html"))

    # 일부 조회가 실패했으면 종료코드 2로 구분한다 (CI에서 성공/부분성공 구분).
    return 2 if stats.failed_operations else 0


if __name__ == "__main__":
    raise SystemExit(main())
