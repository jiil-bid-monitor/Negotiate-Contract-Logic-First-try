"""싱크로율 — 신규 공고 과업 프로필과 과거 실적 150건의 가중 코사인 중 최고값.

과업 프로필 코드(`evaluation/task_profile.py`)와 과거 실적 프로필은 비공개 레포
jiil-past-contracts에 있다. 경로는 환경변수 `JIIL_REPO`(기본: 이 레포 옆 폴더).
"""

from __future__ import annotations

import json
import re
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# 과거 실적끼리(자기 자신 제외) 최고 유사도 분포: 하위 10% 0.59 · 중앙 0.72 · 상위 10% 0.84
# (2026-09-29, 150건). 과거 실적 대부분이 넘는 선을 "높음"으로 잡았다.
HIGH = 0.65
BORDER = 0.45


def level(score: float | None) -> str:
    if score is None:
        return "판정 불가"
    if score >= HIGH:
        return "높음"
    if score >= BORDER:
        return "경계선"
    return "낮음"


def default_jiil_repo() -> Path:
    return Path(os.environ.get("JIIL_REPO") or ROOT.parent / "jiil-past-contracts")


# 과업 설명이 아닌 줄 — 인력 학력·경력 기준표 등(실측: "고등학교 졸 : 15년이상"이 '교육청·학교' 근거로 잡힘)
_NOT_TASK_LINE = re.compile(r"(?:고등학교|대학교?|학사|석사|박사)\s*졸|\d+\s*년\s*이상|경력\s*\d|학력|자격증")
# "겹친 과업" 발췌는 **확실히 과업을 설명하는 줄만**(2026-10-13 요청 — 입찰참가자격 결격 조항 "…신용유의정보…체납기업으로
# 규제되지 아니한 기관"이 '금융·경제' 과업으로 보였음). ① 과업 동사가 있어야 하고 ② 자격·조건·절차 표현이 있으면 뺀다.
_TASK_VERB = re.compile(
    r"제작|설치|설계|시공|구축|개발|조성|기획|연출|구성|촬영|편집|디자인|리모델링|개선|교체|철거|전시|체험|콘텐츠|납품|구현|적용|"
    r"조형물|영상|미디어|인터[랙렉]티브|실감|그래픽|사인|조명|아트월|놀이|VR|AR"
)
_CONDITION_LINE = re.compile(
    r"입찰|참가\s*자격|자격\s*요건|등록(?:한|된)?\s*(?:자|업체)|법률|법령|시행령|시행규칙|법\s*제\s*\d|조\s*제\s*\d|부정당|제재|"
    r"체납|신용|확인서|증명서|소지|해당하지|중소기업|소상공인|보증|제출|서류|평가|배점|가점|감점|공동수급|하도급|지분|"
    r"계약\s*(?:체결|기간|금액|조건)|지체상금|대가|지급|하자|보험|문의|연락처|담당자|일시|마감|기한|"
    r"(?:위\s*치|장\s*소|대\s*상\s*지|소\s*재\s*지)\s*[:：]"
)


def _is_task_line(line: str) -> bool:
    return bool(_TASK_VERB.search(line)) and not _CONDITION_LINE.search(line) and not _NOT_TASK_LINE.search(line)


@dataclass
class PastIndex:
    """과거 실적 목록 + 프로필. jiil 레포가 없으면 `available=False`로 동작한다."""

    repo: Path
    projects: list[dict[str, Any]] = field(default_factory=list)
    available: bool = False
    error: str | None = None
    _tp: Any = None
    _profiles: list[Any] = field(default_factory=list)
    _idf: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, repo: Path | None = None) -> "PastIndex":
        repo = repo or default_jiil_repo()
        index = cls(repo=repo)
        path = repo / "docs/summaries/past_task_profiles.json"
        if not path.exists():
            index.error = f"과거 실적 프로필 없음: {path} (JIIL_REPO 환경변수로 jiil-past-contracts 경로 지정)"
            return index
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from evaluation import task_profile as tp  # jiil 레포

        index._tp = tp
        index.projects = json.loads(path.read_text(encoding="utf-8"))
        index._profiles = [tp.TaskProfile(tags=p["tags"], counts=p.get("counts", {})) for p in index.projects]
        index._idf = tp.idf_weights(index._profiles)
        index.available = True
        return index

    def score(self, title: str, text: str = "", top: int = 5) -> dict[str, Any]:
        """공고명(+과업 원문)으로 싱크로율과 가장 비슷한 과거 실적을 돌려준다.

        원문이 없으면 공고명만으로 프로필을 만든다 — 태그가 한두 개뿐이라 점수가 쉽게
        튀므로 `basis`로 구분해 화면에 표시한다.
        """
        if not self.available:
            return {"score": None, "level": "판정 불가", "basis": "과거 실적 없음", "tags": {}, "top": []}
        tp = self._tp
        # 첨부 구분 줄("=== 파일명 ===")과 "나라장터"(과업 사전이 '장터'를 전통시장으로 잡는다)는 과업 내용이 아니다
        text = re.sub(r"^\s*===.*===\s*$", "", text or "", flags=re.M).replace("나라장터", "나라 조달시스템")
        profile = tp.build_profile([text, title], title=title)
        basis = "과업 원문" if text.strip() else "공고명만"
        if not profile.tag_set():
            return {"score": None, "level": "판정 불가", "basis": basis, "tags": {}, "top": []}
        ranked = sorted(
            ((tp.similarity(profile, p, self._idf), i) for i, p in enumerate(self._profiles)), reverse=True
        )
        mine = profile.tag_set()
        top_rows = []
        for s, i in ranked[:top]:
            shared = sorted(mine & self._profiles[i].tag_set())
            top_rows.append({
                "index": i,
                "year": self.projects[i].get("year"),
                "title": self.projects[i].get("title"),
                "score": round(s, 3),
                "shared": [t.split("/", 1)[1] for t in shared][:10],
            })
        # 가장 비슷한 실적과 겹치는 과업 항목마다 공고 원문에서 그 항목을 잡은 줄(근거)을 발췌한다 — 무게가 큰 칸
        # (시설·주제·콘텐츠·설비 …)부터, 같은 줄은 한 번만. 검토 필요 카드의 "과업 유사" 팝업에 쓴다(2026-10-01 요청).
        if top_rows and text.strip():
            weight = getattr(tp, "FACET_WEIGHT", {})
            keys = sorted(mine & self._profiles[ranked[0][1]].tag_set(),
                          key=lambda k: (-weight.get(k.split("/", 1)[0], 0), k))  # 같은 무게면 이름순 — 수집마다 순서가 바뀌지 않게
            excerpts, seen = [], set()
            for k in keys:
                line = (getattr(profile, "evidence", {}).get(k) or "").strip()
                if not line or line.startswith("(사업명)") or line in seen or not _is_task_line(line):
                    continue
                seen.add(line)
                excerpts.append({"tag": k.split("/", 1)[1], "text": line})
                if len(excerpts) >= 4:
                    break
            top_rows[0]["excerpts"] = excerpts
        best = ranked[0][0] if ranked else 0.0
        return {
            "score": round(best, 3),
            "level": level(best),
            "basis": basis,
            "tags": {f: list(ts) for f, ts in profile.tags.items()},
            "top": top_rows,
        }

    def public_list(self) -> list[dict[str, Any]]:
        return [
            {
                "index": i,
                "year": p.get("year"),
                "title": p.get("title"),
                "overview": p.get("overview", ""),
                "exhibition": p.get("exhibition", ""),
                "tags": p.get("tags", {}),
            }
            for i, p in enumerate(self.projects)
        ]
