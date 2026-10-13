"""카드의 자격 배지·팝업용 자료 — 자격요건(업종 4자리) / 세부품명번호(10자리) 두 부문.

판정 자체(`QualificationResult`)는 건드리지 않고 **보여주는 방식만** 정한다.

- 미보유 쪽은 **요건 단위**로 센다: "A 또는 B"면 둘 중 하나만 있으면 되는 요건 1건.
- 충족 쪽은 **보유해서 요건을 채운 자격** 단위로 센다 — 같은 자격이 여러 요건을 채우면
  (예: 1469가 "1469 또는 4442", "1469 또는 4444" 두 요건을 모두 채움) 한 번만 나온다.
- 충족한 자격은 코드 자릿수대로 부문을 나눈다(10자리 → 세부품명번호, 그 밖 → 자격요건).
- 미보유 요건은 코드가 **전부 10자리**일 때만 세부품명번호 부문. 업종·품목이 섞인
  "다음 중 하나" 요건은 자격요건 부문에 두고, 항목마다 [업종]/[품명] 표시를 붙인다.
- 요건이 없으면 "제한 없음"(정보를 봤는데 없음) / "미확인"(볼 정보가 없었음)으로 구분한다.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Iterable

from nego.report import _dedupe_names_preferring_code, _display_name

_TRAILING_CODE = re.compile(r"\((\d{4}|\d{10})\)\s*$")
_NORM = re.compile(r"[\s·.,/()\-]")

SECTIONS = (("industry", "자격요건"), ("product", "세부품명번호"))


def _code(label: str) -> str | None:
    m = _TRAILING_CODE.search(label)
    return m.group(1) if m else None


def _base(label: str) -> str:
    return _NORM.sub("", _TRAILING_CODE.sub("", label))


def held_lookup(held_raw: dict) -> tuple[set[str], set[str]]:
    """보유 자격 → (코드 집합, 정규화한 이름 집합)."""
    codes, names = set(), set()
    for key in ("heldProducts", "heldIndustries"):
        for entry in held_raw.get(key, []) or []:
            if entry.get("code"):
                codes.add(str(entry["code"]).strip())
            if entry.get("name"):
                names.add(_NORM.sub("", str(entry["name"])))
                # 분야 없이 적은 통칭("소프트웨어사업자")도 보유로 — 보유 이름이 "소프트웨어사업자(컴퓨터관련서비스사업)"
                if "(" in str(entry["name"]):
                    names.add(_NORM.sub("", str(entry["name"]).split("(")[0]))
    return codes, names


# 이름 앞에 붙어 나오는 분류 머리말 — "G2B분류번호 교육훈련장비(6010999901)"
_NAME_PREFIX = re.compile(r"^(?:G2B\s*)?(?:물품\s*)?(?:세부\s*)?(?:분류\s*번호|품명\s*번호|품명)\s*[:：]?\s*")


def _clean_label(label: str) -> str:
    code = _code(label)
    if not code:
        return label
    name = _TRAILING_CODE.sub("", label).strip()
    name = _NAME_PREFIX.sub("", name).strip()
    return f"{name}({code})" if name else code


def _best_label(labels: list[str]) -> str:
    """같은 코드의 여러 표기 중 이름이 가장 온전한 것 — "육훈련장비"(잘린 것)보다 "교육훈련장비"."""
    return max(labels, key=lambda l: len(_TRAILING_CODE.sub("", l)))


def _items(group, held: tuple[set[str], set[str]]) -> list[dict[str, Any]]:
    labels = _dedupe_names_preferring_code(list(dict.fromkeys(_clean_label(_display_name(n)) for n in group.allowed_names)))
    # 같은 코드가 한 요건 안에 두 번 나오면(표기만 다름) 하나로
    by_code: dict[str, list[str]] = {}
    for label in labels:
        by_code.setdefault(_code(label) or label, []).append(label)
    labels = [_best_label(v) for v in by_code.values()]
    codes, names = held
    out = []
    for label in labels:
        code = _code(label)
        is_held = (code in codes) if code else (_base(label) in names)
        out.append({"label": label, "code": code, "kind": "product" if code and len(code) == 10 else "industry",
                    "held": is_held})
    return out


def _plain(label: str) -> str:
    return re.sub(r"[\s·ㆍ]+", "", _TRAILING_CODE.sub("", label))


def _named_alternatives(items: list[dict[str, Any]], doc_text: str) -> list[dict[str, Any]]:
    """대신 인정 업종(허용업종) 중 **공고문이 "A 또는 B"로 직접 적은 것**만 고른다.

    나라장터 허용업종은 공고문과 어긋날 때가 있어 기본으로는 숨기지만(폐기물 공고: 공고문은 "1227 반드시"),
    공고문이 "토목공사업 또는 토목건축공사업을 등록한 자"처럼 적었으면 보여줘야 한다(2026-09-30 제보).
    공백을 지운 원문에서 대표 이름과 대체 이름이 한 항목 안에서 "또는"으로 이어져 있는지 본다(순서 무관).
    "A[4440] 또는 B[4442] 또는 C[4443] 또는 D[4444]로 등록한 자"처럼 "또는"이 사슬로 이어져도 된다
    (2026-09-30 제보: 국립울진해양과학관 — 4440만 보이고 4442·4443·4444가 빠졌음). 사이에 항목 번호
    (①, 2), 가.)나 마침표가 끼면 다른 항목이라 잇지 않는다 — 폐기물 공고 "① 1227 ② 종합처분업"은 계속 숨김.
    """
    if not doc_text or len(items) < 2:
        return []
    # "토목·건축공사업"처럼 가운뎃점을 넣어 적기도 한다(2026-10-01 제보: 고이분교 리모델링) — 공백과 함께 지운다
    text = re.sub(r"[\s·ㆍ]+", "", doc_text)
    first = re.escape(_plain(items[0]["label"]))
    out = []
    for alt in items[1:]:
        a = re.escape(_plain(alt["label"]))
        between = r"(?:(?![.。①-⑳]|\d\)|[가-하]\.)[^\n]){0,160}?"
        pattern = f"(?:{first}(?P<g1>{between}){a}|{a}(?P<g2>{between}){first})"
        for m in re.finditer(pattern, text):
            gap = m.group("g1") if m.group("g1") is not None else m.group("g2")
            if "또는" in gap and not re.search(r"과|와|및|모두", re.sub(r"\([^)]*\)|\[[^\]]*\]", "", gap).replace("또는", "")):
                out.append(alt)
                break
    return out


def _combo_req(group, held: tuple[set[str], set[str]], doc_text: str = "") -> dict[str, Any]:
    """"아래 조합 중 하나" 요건 — 조합마다 행(대표 이름 + 대신 인정 이름들)과 보유 여부."""
    combos = []
    for rows in group.combos:
        out = []
        for row in rows:
            row_items = _items(SimpleNamespace(allowed_names=row), held)
            if not row_items or any(r["label"] == row_items[0]["label"] for r in out):
                continue  # 같은 면허가 한 조합에 두 번 — 한 번만
            shown = [row_items[0]] + _named_alternatives(row_items, doc_text)
            via = None
            if not any(i["held"] for i in shown):
                via = next((i["label"] for i in row_items[1:] if i["held"]), None)
            # 공고문에 없는 대신 인정 업종은 보여주지 않는다(공고문 모양 유지) — 그걸로 채웠을 때만 무엇으로 채웠는지 적는다
            out.append({"label": " 또는 ".join(i["label"] for i in shown), "held": any(i["held"] for i in row_items),
                        "via": f"{via} (대체 인정)" if via else None})
        if out and not any([r["label"] for r in c["rows"]] == [r["label"] for r in out] for c in combos):
            combos.append({"rows": out, "held": all(r["held"] for r in out)})
    # 더 따야 할 면허 수 = 가장 덜 모자란 조합의 미보유 면허 수
    need = min((sum(not r["held"] for r in c["rows"]) for c in combos), default=1)
    return {"any_of": True, "combos": combos, "items": [], "need": need}


def _section_of(items: list[dict[str, Any]]) -> str:
    coded = [i for i in items if i["code"]]
    return "product" if coded and len(coded) == len(items) and all(i["kind"] == "product" for i in items) else "industry"


def build(qualification, held: tuple[set[str], set[str]], had_source: dict[str, bool],
          doc_text: str = "") -> list[dict[str, Any]]:
    """부문 2개를 항상 돌려준다. `had_source[key]`: 그 부문 요건을 찾아볼 정보가 있었는지.

    충족 쪽은 **보유 자격 하나하나를 그 코드 자릿수로** 부문에 넣는다 — 첨부문서의 "어느 하나"
    요건처럼 업종·품목이 한 묶음이어도, 10자리 품목(예: 실물모형및전시물 6010989901)은
    세부품명번호 부문에, 4자리 업종은 자격요건 부문에 나온다.
    미보유 쪽은 요건 단위라 나눌 수 없어, 전부 10자리일 때만 세부품명번호 부문에 둔다.
    """
    missing: dict[str, list] = {k: [] for k, _ in SECTIONS}
    # 충족한 요건도 미보유와 같은 모양으로 — 팝업에서 요건 전체를 초록 ✓ / 붉은 ✗로 한 번에 보여준다(2026-09-30 요청)
    satisfied: dict[str, list] = {k: [] for k, _ in SECTIONS}
    held_by: dict[str, list[str]] = {k: [] for k, _ in SECTIONS}
    satisfied_any: dict[str, bool] = {k: False for k, _ in SECTIONS}
    # 같은 요건이 여러 번 잡히면 한 번만 — **코드로** 비교한다. 이름으로 비교하면 "G2B분류번호 교육훈련장비
    # (6010999901)"와 "육훈련장비(6010999901)"처럼 표기만 다른 같은 요건이 두 번 나온다(2026-09-30 제보).
    by_sig: dict[tuple, list] = {}
    order: list[tuple[str, tuple]] = []
    # 면허제한 API의 "반드시" 요건(대표 면허 코드). 첨부 참가자격 판정이 같은 면허를 또 잡으면("건축공사업(또는
    # 토목·건축공사업)을 등록한 자") 같은 요건이 두 번 나온다(2026-10-01 제보: 건축공사업 ×2) — API 쪽만 남긴다.
    api_primary = {
        _code(_clean_label(_display_name(g.rows[0][0])))
        for g in list(qualification.missing_groups) + list(qualification.satisfied_groups)
        if len(g.rows or []) == 1 and g.rows[0]
    } - {None}
    # 면허제한 API 요건마다 대상 코드 묶음(대신 인정 업종 포함). 첨부 판정의 "A 또는 B 또는 C"가 이 안에 다 들어가면 같은
    # 요건이다(2026-10-13 제보 R26BK01755562: API "1478·4119·4125·국유림영림단 중 하나" + 첨부 "1478·4119·4125 중 하나"가
    # 따로 나와 "자격요건 2건"으로 보였음) — 더 온전한 API 쪽만 남긴다.
    api_code_sets = [
        {_code(_clean_label(_display_name(n))) for n in g.allowed_names} - {None}
        for g in list(qualification.missing_groups) + list(qualification.satisfied_groups)
        if g.rows or getattr(g, "combos", None) or str(g.group_no).startswith(("택1", "조합"))
    ]
    for state, source in (("missing", qualification.missing_groups), ("satisfied", qualification.satisfied_groups)):
        for g in source:
            if getattr(g, "combos", None):
                combo = _combo_req(g, held, doc_text)
                if state == "missing":
                    missing["industry"].append(combo)
                else:  # 채운 조합의 면허들을 보유 자격으로 적는다
                    satisfied["industry"].append(combo)
                    done = next(c for c in combo["combos"] if c["held"])
                    held_by["industry"].extend(r["label"] for r in done["rows"])
                    satisfied_any["industry"] = True
                continue
            if len(g.rows or []) == 1 and len(g.rows[0]) > 1:
                # "반드시" 요건인데 나라장터가 대신 인정하는 업종(허용업종)을 같이 준 경우 — 대표 면허만 보여준다
                items = _items(SimpleNamespace(allowed_names=g.rows[0]), held)
                if not items:
                    continue
                first = items[0]
                named = _named_alternatives(items, doc_text)
                if named:
                    # 공고문이 "A 또는 B"로 적은 대신 인정 업종은 함께 보여준다 → "아래 N개 중 1개 이상"
                    req = {"any_of": True, "items": [first] + named}
                    hidden_held = [i for i in items[1:] if i["held"] and i not in named]
                    if hidden_held and not any(i["held"] for i in req["items"]):
                        req["items"] = [dict(first, held=True, via=f"{hidden_held[0]['label']} (대체 인정)")] + named
                    if state == "missing":
                        missing[_section_of(items)].append(req)
                    else:
                        satisfied[_section_of(items)].append(req)
                        mine = [i for i in items if i["held"]] or [first]
                        held_by[mine[0]["kind"]].append(mine[0]["label"])
                        satisfied_any[mine[0]["kind"]] = True
                    continue
                # 공고문에 없는 대신 인정 업종은 판정에만 쓰고 보여주지 않는다(2026-09-30 사용자 결정 — 공고문 모양 유지)
                req = {"any_of": False, "items": [dict(first, held=any(i["held"] for i in items))]}
                if state == "missing":
                    missing[_section_of(items)].append(req)
                else:
                    mine = [i for i in items if i["held"]] or [first]
                    label = mine[0]["label"] if mine[0] is first else f"{mine[0]['label']} (대체 인정)"
                    if mine[0] is not first:
                        req["items"][0]["via"] = label
                    satisfied[_section_of(items)].append(req)
                    held_by[mine[0]["kind"]].append(label)
                    satisfied_any[mine[0]["kind"]] = True
                continue
            items = _items(g, held)
            if not items:
                continue
            if not g.rows and g.group_no != "held" and any(i["code"] in api_primary for i in items):
                continue
            att_codes = {i["code"] for i in items}
            if (not g.rows and g.group_no != "held" and not str(g.group_no).startswith(("택1", "조합"))
                    and None not in att_codes and any(att_codes <= api for api in api_code_sets)):
                continue
            sig = tuple(sorted(i["code"] or i["label"] for i in items))
            if sig in by_sig:
                # 이미 본 요건 — 더 온전한 이름이 있으면 그걸로 바꿔 둔다
                for kept in by_sig[sig]:
                    for other in items:
                        if (other["code"] or other["label"]) == (kept["code"] or kept["label"]):
                            kept["label"] = _best_label([kept["label"], other["label"]])
                continue
            by_sig[sig] = items
            # 첨부 판정의 "held" 그룹은 요건이 아니라 참가자격 절에서 찾은 보유 코드 목록이다
            order.append((state, sig, g.group_no == "held"))
    for state, sig, held_list in order:
        items = by_sig[sig]
        if state == "missing":
            missing[_section_of(items)].append(items)
            continue
        if held_list:
            for i in items:
                satisfied[i["kind"]].append({"any_of": False, "items": [i], "why": "보유 확인"})
        else:
            satisfied[_section_of(items)].append({"any_of": len(items) > 1, "items": items})
        mine = [i for i in items if i["held"]]
        if mine:
            for i in mine:
                held_by[i["kind"]].append(i["label"])
                satisfied_any[i["kind"]] = True
        else:  # 보유 표시를 못 붙인 요건(이름 표기 차이)은 요건 자체를 적는다
            key = _section_of(items)
            held_by[key].append(" 또는 ".join(i["label"] for i in items))
            satisfied_any[key] = True
    # 보유 목록도 같은 코드는 한 번만 (가장 온전한 이름으로)
    for key in held_by:
        grouped: dict[str, list[str]] = {}
        for label in held_by[key]:
            grouped.setdefault(_code(label) or label, []).append(label)
        held_by[key] = [_best_label(v) for v in grouped.values()]

    parts = []
    for key, name in SECTIONS:
        miss = missing[key]
        if miss:
            status = "미달"
        elif satisfied_any[key]:
            status = "충족"
        else:
            status = "제한 없음" if had_source.get(key) else "미확인"
        reqs = [items if isinstance(items, dict) else {"any_of": len(items) > 1, "items": items} for items in miss]
        # 충족 요건 중복 제거 — 같은 코드가 API 요건과 첨부 "보유 확인"에 두 번 잡히면 요건 쪽만 남긴다
        ok_reqs, seen = [], set()
        for r in sorted(satisfied[key], key=lambda r: r.get("why") == "보유 확인"):
            labels = ([row["label"] for c in r["combos"] for row in c["rows"]] if r.get("combos")
                      else [i["label"] for i in r["items"]])
            sig = tuple(sorted(_code(l) or l for l in labels))
            if r.get("why") == "보유 확인" and (sig[0] in seen):
                continue
            if sig in seen:
                continue
            seen.add(sig)
            seen.update(sig)
            ok_reqs.append(r)
        parts.append({
            "key": key, "name": name, "status": status,
            # "N건"이 무엇을 세는지 안 보여서(2026-09-30 제보) — 자격을 갖추려면 최소 몇 개를 더 갖춰야 하나로 센다.
            # "반드시"·"N개 중 1개 이상" 요건은 1개, "조합 중 하나"는 가장 덜 모자란 조합의 미보유 수.
            "need": sum(r.get("need", 1) for r in reqs),
            "missing": reqs,
            "satisfied": ok_reqs,
            "held": held_by[key],
        })
    return parts


def flat_missing(parts: Iterable[dict[str, Any]]) -> list[str]:
    def line(req: dict[str, Any]) -> str:
        if req.get("combos"):
            return " 또는 ".join("(" + " + ".join(r["label"] for r in c["rows"]) + ")" for c in req["combos"])
        return " 또는 ".join(i["label"] for i in req["items"])
    return [line(req) for p in parts for req in p["missing"]]
