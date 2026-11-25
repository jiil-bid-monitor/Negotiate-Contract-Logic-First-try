"""낙찰 후 서류 폴더를 NAS ↔ 사내 공유 폴더로 맞추기(서버 PC, 2026-11-17 사용자 결정).

NAS 웹앱 컨테이너는 jiil-webapp 공유 폴더만 볼 수 있어 사내 공유 폴더에 직접 못 쓴다 → 두 공유 폴더를 다 여는 서버 PC가
1분마다 맞춘다. 담당자는 사내 공유 폴더 쪽 파일을 고치고(기준), 웹앱 '서류 현황'은 NAS 쪽을 읽으므로 고친 내용이 1분 안에 반영된다.
규칙: 한쪽에만 있는 파일은 다른 쪽으로 복사, 둘 다 있으면 수정 시각이 늦은 쪽이 이김(copy2로 시각을 같이 옮겨 되풀이 안 함), 지우지 않음.
사업 폴더(11/25 사용자): .env POSTAWARD_PROJECT_ROOT(사내 공유 '002 진행프로젝트')가 있으면 계약까지 온 공고(폴더에 1_계약이 있음)만
'입찰/번호_공고명'과 맞춤(끝나서 'YYYY 프로젝트/실행완료'로 옮긴 것은 그곳)(적격심사 단계 폴더는 NAS에만). 탈락한 공고 폴더는 NAS 웹앱이 탈락으로 옮기고(postaward.archive_lost)
여기서 사내 공유 쪽 것도 탈락으로 옮김. NAS의 탈락 폴더는 30일 뒤 지움(postaward.purge_lost, 11/25 사용자).
켜기: 서버 PC .env의 POSTAWARD_SHARE_DIR(사내 공유 폴더 쪽 '낙찰후서류' 위치). NAS 쪽은 POSTAWARD_NAS_DIR(기본 \\\\jiilserver\\jiil-webapp\\낙찰후서류).
shortcut: 1분마다 전체 비교 — 공고 폴더가 수백 개로 늘어 느려지면 바뀐 폴더만 보게 바꿀 것. 리빌드로 NAS가 사내 공유 폴더를 직접 보게 되면 이 동기화는 끔.
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import threading
from pathlib import Path

NAS_DIR = Path(r"\\jiilserver\jiil-webapp\낙찰후서류")
GROUPS = ("공사", "물품·용역")  # postaward.GROUPS의 폴더 이름(표준틀 폴더 _표준틀은 안 맞춤)
LOST_DIR = "탈락"
BID_DIR = "입찰"
SLACK = 2.0  # 초 — 공유 폴더마다 시각 정밀도가 달라 이만큼은 같은 시각으로 봄


def _skip(p: Path) -> bool:
    return p.name.startswith(("~$", ".")) or p.name in ("Thumbs.db", "desktop.ini")


def sync_once(nas: Path, share: Path) -> dict:
    """한 번 맞춤 → {'to_share': n, 'to_nas': m, 'errors': k}."""
    done = {"to_share": 0, "to_nas": 0, "errors": 0}
    drop_lost(nas, share)
    for group in GROUPS:
        _sync_dir(nas / group, share / group, done)
    return done


def _sync_dir(a_root: Path, b_root: Path, done: dict) -> None:
    rels = set()
    for root in (a_root, b_root):
        if root.is_dir():
            rels |= {p.relative_to(root) for p in root.rglob("*") if p.is_file() and not _skip(p)}
    for rel in sorted(rels):
        a, b = a_root / rel, b_root / rel
        try:
            if a.exists() and b.exists():
                ta, tb = a.stat().st_mtime, b.stat().st_mtime
                if abs(ta - tb) <= SLACK:
                    continue
                src, dst, key = (b, a, "to_nas") if tb > ta else (a, b, "to_share")
            else:
                src, dst, key = (a, b, "to_share") if a.exists() else (b, a, "to_nas")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            done[key] += 1
        except OSError as err:  # 엑셀에서 열려 있는 파일 등 — 다음 차례에 다시
            done["errors"] += 1
            logging.debug("낙찰후서류 맞추기 건너뜀 %s: %s", rel, err)


def free_path(p: Path) -> Path:
    """같은 이름이 있으면 _2, _3 … 을 붙임(덮어쓰지 않게)."""
    n, q = 2, p
    while q.exists():
        q, n = p.with_name(f"{p.name}_{n}"), n + 1
    return q


def drop_lost(nas: Path, share: Path) -> None:
    """NAS에서 탈락으로 옮긴 공고 폴더가 사내 공유 쪽에 남아 있으면 거기서도 탈락으로(안 옮기면 다음 맞춤 때 되살아남)."""
    lost = nas / LOST_DIR
    for f in lost.iterdir() if lost.is_dir() else ():
        for group in GROUPS:
            if f.is_dir() and (share / group / f.name).is_dir() and not (nas / group / f.name).exists():
                try:
                    (share / LOST_DIR).mkdir(parents=True, exist_ok=True)
                    shutil.move(str(share / group / f.name), str(free_path(share / LOST_DIR / f.name)))
                except OSError as err:  # 엑셀에서 열려 있음 — 다음 차례에
                    logging.debug("탈락 폴더 옮기기 건너뜀 %s: %s", f.name, err)


def project_target(project_root: Path, name: str) -> Path:
    """계약까지 온 공고의 사업 폴더 — 진행 중은 연도와 상관없이 '입찰' 한 곳(11/25 사용자: 해를 넘겨도 예전 연도를 찾아가지 않게).
    담당자가 'YYYY 프로젝트/실행완료'로 옮긴 사업은 그곳."""
    for found in sorted(project_root.glob(f"20?? 프로젝트/실행완료/{glob.escape(name)}"), reverse=True):
        return found
    return project_root / BID_DIR / name


def sync_projects(nas: Path, project_root: Path) -> dict:
    """계약까지 온 공고 폴더 ↔ 사업 폴더."""
    done = {"to_share": 0, "to_nas": 0, "errors": 0}
    for group in GROUPS:
        for f in (nas / group).iterdir() if (nas / group).is_dir() else ():
            if (f / "1_계약").is_dir():  # 적격심사 단계(0_적격심사·공고문서만)는 NAS에만
                _sync_dir(f, project_target(project_root, f.name), done)
    return done


class AwardShareSync(threading.Thread):
    def __init__(self, nas: Path, share: Path | None, every: int = 60, project: Path | None = None):
        super().__init__(daemon=True, name="award-share-sync")
        self.nas, self.share, self.every, self.project = nas, share, every, project
        self.stop = threading.Event()

    @classmethod
    def from_env(cls) -> "AwardShareSync | None":
        share = os.environ.get("POSTAWARD_SHARE_DIR", "").strip()
        project = os.environ.get("POSTAWARD_PROJECT_ROOT", "").strip()
        if not share and not project:
            return None
        return cls(Path(os.environ.get("POSTAWARD_NAS_DIR", "").strip() or NAS_DIR), Path(share) if share else None,
                   project=Path(project) if project else None)

    def run(self) -> None:
        while not self.stop.is_set():
            if self.nas.is_dir():
                d = {"to_share": 0, "to_nas": 0, "errors": 0}
                if self.project is not None:  # 사업 폴더를 쓰면 예전 시험 폴더(POSTAWARD_SHARE_DIR)는 안 맞춤
                    if self.project.is_dir():
                        d = sync_projects(self.nas, self.project)
                else:
                    self.share.mkdir(parents=True, exist_ok=True)
                    d = sync_once(self.nas, self.share)
                if d["to_share"] or d["to_nas"]:
                    logging.info("낙찰후서류 맞춤: 사내 공유로 %d · NAS로 %d · 건너뜀 %d", d["to_share"], d["to_nas"], d["errors"])
            self.stop.wait(self.every)


def selfcheck() -> None:
    import tempfile
    import time

    with tempfile.TemporaryDirectory() as d:
        nas, share = Path(d) / "nas", Path(d) / "share"
        (nas / "공사" / "R1_A" / "0_적격심사").mkdir(parents=True)
        (nas / "공사" / "R1_A" / "0_적격심사" / "적격심사서류.xlsx").write_text("v1", encoding="utf-8")
        (nas / "_표준틀").mkdir()
        (nas / "_표준틀" / "t.xlsx").write_text("t", encoding="utf-8")
        assert sync_once(nas, share)["to_share"] == 1
        f_share = share / "공사" / "R1_A" / "0_적격심사" / "적격심사서류.xlsx"
        assert f_share.read_text(encoding="utf-8") == "v1" and not (share / "_표준틀").exists()
        assert sync_once(nas, share) == {"to_share": 0, "to_nas": 0, "errors": 0}  # 시각이 같아 되풀이 안 함
        f_share.write_text("담당자가 채움", encoding="utf-8")
        t = time.time() + 10
        os.utime(f_share, (t, t))
        assert sync_once(nas, share)["to_nas"] == 1  # 사내 공유 쪽에서 고친 것이 NAS로
        assert (nas / "공사" / "R1_A" / "0_적격심사" / "적격심사서류.xlsx").read_text(encoding="utf-8") == "담당자가 채움"
        (share / "공사" / "R1_A" / "~$적격심사서류.xlsx").write_text("lock", encoding="utf-8")
        assert sync_once(nas, share)["to_nas"] == 0  # 엑셀 임시 파일은 안 옮김
        (nas / "공사" / "R1_A" / "2_착공").mkdir()
        (nas / "공사" / "R1_A" / "2_착공" / "착공서류.xlsx").write_text("new", encoding="utf-8")
        assert sync_once(nas, share)["to_share"] == 1  # 낙찰 뒤 웹앱이 더한 파일도 사내 공유로
        # 탈락: NAS에서 탈락으로 옮기면 사내 공유 쪽도 옮기고 되살리지 않음
        (nas / LOST_DIR).mkdir()
        shutil.move(str(nas / "공사" / "R1_A"), str(nas / LOST_DIR / "R1_A"))
        assert sync_once(nas, share)["to_nas"] == 0
        assert not (nas / "공사" / "R1_A").exists() and not (share / "공사" / "R1_A").exists() and (share / LOST_DIR / "R1_A").is_dir()
        # 사업 폴더: 계약까지 온 공고만, 'YYYY 프로젝트'가 있을 때만
        root, r2 = Path(d) / "002", nas / "공사" / "R2_B"
        (r2 / "0_적격심사").mkdir(parents=True)
        (r2 / "0_적격심사" / "q.xlsx").write_text("q", encoding="utf-8")
        assert sync_projects(nas, root)["to_share"] == 0  # 적격심사 단계
        (r2 / "1_계약").mkdir()
        assert sync_projects(nas, root)["to_share"] == 1
        bid = root / BID_DIR / "R2_B"
        assert (bid / "0_적격심사" / "q.xlsx").is_file()
        (bid / "2.txt").write_text("담당자", encoding="utf-8")
        assert sync_projects(nas, root)["to_nas"] == 1 and (r2 / "2.txt").is_file()
        moved = root / "2027 프로젝트" / "실행완료" / "R2_B"  # 끝나서 담당자가 옮김 — 따라가고 '입찰'에 되살리지 않음
        moved.parent.mkdir(parents=True)
        bid.rename(moved)
        (r2 / "3.txt").write_text("웹앱", encoding="utf-8")
        assert sync_projects(nas, root)["to_share"] == 1 and (moved / "3.txt").is_file() and not bid.exists()
    print("ok")


if __name__ == "__main__":
    selfcheck()
