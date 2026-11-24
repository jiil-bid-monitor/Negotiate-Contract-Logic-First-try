"""기관별 따로 수집 규칙(`config/org_watch.json`) — 웹앱 수집 규칙(keywords.json)과 따로 쓴다.

2026-11-24 한전KDN 전시·홍보·영상 사업 보고서를 계기로 만듦: 한전KDN이 해마다 내는 전시부스·홍보영상·홍보물·홍보관·사옥 공간 공사는
웹앱 규칙으로는 빠졌음(제외 키워드 '전시부스', 맞는 관심 키워드 없음). 그래서 기관을 정하고 그 기관 공고에만 따로 낱말 묶음을 건다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import Notice

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "org_watch.json"


def squash(text: str | None) -> str:
    return re.sub(r"\s", "", text or "").lower()


@dataclass
class OrgRule:
    org: str
    names: list[str]
    groups: dict[str, list[str]]
    group_work_types: dict[str, list[str]] = field(default_factory=dict)
    exclude: list[str] = field(default_factory=list)

    def owns(self, notice: Notice) -> str | None:
        """공고기관·수요기관 중 이 기관 이름이 든 쪽(없으면 None)."""
        words = [squash(n) for n in self.names if squash(n)]
        for name in (notice.notice_institution, notice.demand_institution):
            if name and any(w in squash(name) for w in words):
                return name
        return None

    def match(self, notice: Notice) -> tuple[dict[str, list[str]], list[str]]:
        """(걸린 묶음 → 낱말, 걸린 제외 낱말). 묶음이 비면 대상 아님, 제외 낱말이 있으면 뺌."""
        title = squash(notice.title)
        hits: dict[str, list[str]] = {}
        for group, words in self.groups.items():
            allowed = self.group_work_types.get(group)
            if allowed and notice.work_type not in allowed:
                continue
            found = [w for w in words if squash(w) and squash(w) in title]
            if found:
                hits[group] = found
        excluded = [w for w in self.exclude if squash(w) and squash(w) in title]
        return hits, excluded


def load_rules(path: Path = DEFAULT_PATH) -> dict[str, OrgRule]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = {}
    for org, body in (raw.get("orgs") or {}).items():
        rules[org] = OrgRule(
            org=org,
            names=list(body.get("names") or [org]),
            groups={k: list(v) for k, v in (body.get("groups") or {}).items()},
            group_work_types={k: list(v) for k, v in (body.get("groupWorkTypes") or {}).items()},
            exclude=list(body.get("exclude") or []),
        )
    return rules
