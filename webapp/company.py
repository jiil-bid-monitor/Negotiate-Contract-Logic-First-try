"""회사별 상태 — 웹앱 첫 화면의 회사 고르기(2026-11-24 사용자 요청: 지일·그리다).

같은 서버·같은 수집 규칙을 쓰고 보유 자격(config/companies.json의 held 파일)만 다르다. 회사마다 수집 결과(sqlite)·
판단 기록(participation.json)·공고문 세부품명 확인을 따로 둔다 — 지일은 예전 data/ 그대로, 다른 회사는 data/<id>/.
화면은 요청마다 ?co=<id>를 붙이고(없으면 지일), 서버는 `App.company(q)`로 이 묶음을 고른다.
"""

from __future__ import annotations

from typing import Any, Callable

from nego.config import ConfigError, load_config


class Company:
    def __init__(self, cid: str, name: str, full_name: str, store: Any, team: Any, collector: Any, docscan: Any):
        self.id = cid
        self.name = name
        self.full_name = full_name
        self.store = store
        self.team = team
        self.collector = collector
        self.docscan = docscan
        self.push: Any = None  # 서버 PC → NAS 결과 보내기(webapp/resultpush.py) — main()이 켠다
        self.bidlog: Any = None  # 투찰 기록(지일은 App.bidlog — NAS에선 서버 PC 사본과 합친 것으로 바뀌어 App이 들고 있음)
        self._run_cache: tuple[int | None, dict[str, Any] | None] = (None, None)
        self._held_raw: dict[str, Any] | None = None
        collector.company_name = name

    @property
    def default(self) -> bool:
        return self.id == "jiil"

    def config(self):
        return load_config(company=self.id)

    def latest_run(self) -> dict[str, Any] | None:
        """최신 결과(수 MB JSON)를 요청마다 다시 읽지 않게 결과 번호가 같으면 들고 있는 것을 쓴다."""
        run_id = self.store.run_id_if_exists()
        if run_id is None:
            return None
        if self._run_cache[0] != run_id:
            self._run_cache = (run_id, self.store.run(run_id))
        return self._run_cache[1]

    def held_raw(self) -> dict[str, Any]:
        if self._held_raw is None:
            try:
                self._held_raw = load_config(company=self.id).held_raw
            except ConfigError:
                self._held_raw = {}
        return self._held_raw

    def bizno(self) -> str:
        """개찰 결과에서 '우리 회사' 줄을 찾을 사업자번호 — 지일은 예전 그대로(JIIL_BIZNO·지일 파일), 다른 회사는 그 회사 보유 자격 파일."""
        if self.default:
            from nego.bidrecommend import default_own_bizno

            return default_own_bizno()
        return str(self.held_raw().get("companyBizno") or "")

    def titles(self) -> dict[str, str]:
        run = self.store.latest_run() or {}
        return {c.get("notice_no"): c.get("title") or "" for c in (run.get("candidates") or []) + (run.get("rejected") or [])}

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "fullName": self.full_name}


def link_collectors(companies: dict[str, Company]) -> None:
    """수집은 회사를 통틀어 한 번에 하나 — 서로를 peers로 알려 준다."""
    for co in companies.values():
        co.collector.peers = [o.collector for o in companies.values() if o is not co]


def config_loader(cid: str) -> Callable[[], Any]:
    return lambda: load_config(company=cid)
