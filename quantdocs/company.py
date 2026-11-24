"""정량평가서를 어느 회사 자료로 만드는지(2026-11-24 회사 고르기 — 지일·그리다).

지일 = 예전 그대로(잠금 묶음 jiil-quant-docs/vault/company.enc + 그 폴더의 서류). 다른 회사 = 서버 PC의 옆 폴더
`<id>-quant-docs`(예: C:\\Users\\HJE\\나라장터 수집\\grida-quant-docs, .env QUANT_GRIDA_DIR로 바꿈) 안 서류를 그대로 읽음(묶음 없음).
분석·만들기는 작업마다 다른 스레드에서 돌아 ContextVar로 그 작업의 회사를 들고 다닌다(`use`).
"""

from __future__ import annotations

from contextvars import ContextVar

DEFAULT = "jiil"
_CURRENT: ContextVar[str] = ContextVar("quant_company", default=DEFAULT)


def current() -> str:
    return _CURRENT.get()


def use(company: str | None) -> None:
    """이 스레드에서 이 회사 자료로 — 작업 스레드 시작 때 부름."""
    _CURRENT.set(company or DEFAULT)


def held_file() -> str:
    """config 폴더 안 그 회사 보유 자격 파일 이름(nego.config.held_file과 같은 목록)."""
    if current() == DEFAULT:
        return "held_qualifications.json"
    try:
        from nego.config import held_file as nego_held_file

        return _relative(nego_held_file(current()))
    except Exception:  # noqa: BLE001 — 목록에 없으면 지일 파일
        return "held_qualifications.json"


def _relative(path) -> str:
    parts = path.parts
    return "/".join(parts[parts.index("config") + 1:]) if "config" in parts else path.name


def name() -> str:
    try:
        from nego.config import list_companies

        return next((r.get("name") or r["id"] for r in list_companies() if r["id"] == current()), current())
    except Exception:  # noqa: BLE001
        return "지일" if current() == DEFAULT else current()
