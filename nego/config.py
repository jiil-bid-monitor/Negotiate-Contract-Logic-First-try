"""설정 로딩. config/*.json 만 고치면 코드 수정 없이 동작이 바뀐다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .http_client import ApiConfig
from .screen import ScreenConfig

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class ConfigError(Exception):
    pass


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ConfigError(f"설정 파일 JSON 오류 ({path.name}): {err}") from err


@dataclass
class AppConfig:
    screen: ScreenConfig
    held_names: list[str]
    held_raw: dict
    api: ApiConfig
    lookback_days: int
    output_dir: Path
    # 코드 → 이름 사전 (codes.json + code_names.json). 첨부파일이 코드만 적은
    # 미보유 자격에 이름을 붙이는 데 쓴다.
    code_names: dict[str, str] | None = None


def load_config(config_dir: Path | None = None) -> AppConfig:
    directory = config_dir or DEFAULT_CONFIG_DIR

    keywords = _read_json(directory / "keywords.json")
    codes = _read_json(directory / "codes.json")
    held = _read_json(directory / "held_qualifications.json")
    # 선택 파일 — 없어도 동작한다.
    code_names_path = directory / "code_names.json"
    code_names_raw = _read_json(code_names_path) if code_names_path.exists() else {}

    from .qualify import load_code_names, load_held_names

    service_key = os.environ.get("NARA_SERVICE_KEY", "").strip()

    screen = ScreenConfig(
        keywords=list(keywords.get("keywords", [])),
        exclude_keywords=list(keywords.get("excludeKeywords", [])),
        hard_exclude_keywords=list(keywords.get("hardExcludeKeywords", [])),
        exclude_exceptions=dict(keywords.get("excludeKeywordExceptions", {})),
        min_budget_amount=keywords.get("minBudgetAmount"),
        budget_exempt_keywords=list(keywords.get("minBudgetExemptKeywords", [])),
        keyword_skip_when=dict(keywords.get("keywordSkipWhen", {})),
        product_codes=list(codes.get("productCodes", [])),
        industry_codes=list(codes.get("industryCodes", [])),
    )

    api = ApiConfig(
        service_key=service_key,
        timeout_sec=float(os.environ.get("API_TIMEOUT_SEC", "30")),
        max_retries=int(os.environ.get("API_MAX_RETRIES", "4")),
        retry_delay_sec=float(os.environ.get("API_RETRY_DELAY_SEC", "1")),
        num_of_rows=int(os.environ.get("API_NUM_OF_ROWS", "999")),
        max_pages=int(os.environ.get("API_MAX_PAGES", "100")),
        request_interval_sec=float(os.environ.get("API_REQUEST_INTERVAL_SEC", "0")),
    )

    return AppConfig(
        screen=screen,
        held_names=load_held_names(held),
        held_raw=held,
        api=api,
        lookback_days=int(os.environ.get("LOOKBACK_DAYS", "1")),
        output_dir=Path(os.environ.get("OUTPUT_DIR", "output")),
        code_names=load_code_names(codes, code_names_raw),
    )


def redact(text: str, secrets: list[str]) -> str:
    """로그·오류 메시지에서 서비스키를 가린다."""
    out = text
    for secret in secrets:
        if secret and len(secret) > 8:
            out = out.replace(secret, f"{secret[:4]}***{secret[-4:]}")
    return out
