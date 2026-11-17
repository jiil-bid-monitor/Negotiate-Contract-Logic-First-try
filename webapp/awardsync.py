"""낙찰 후 서류 폴더를 NAS ↔ 사내 공유 폴더로 맞추기(서버 PC, 2026-11-17 사용자 결정).

NAS 웹앱 컨테이너는 jiil-webapp 공유 폴더만 볼 수 있어 사내 공유 폴더에 직접 못 쓴다 → 두 공유 폴더를 다 여는 서버 PC가
1분마다 맞춘다. 담당자는 사내 공유 폴더 쪽 파일을 고치고(기준), 웹앱 '서류 현황'은 NAS 쪽을 읽으므로 고친 내용이 1분 안에 반영된다.
규칙: 한쪽에만 있는 파일은 다른 쪽으로 복사, 둘 다 있으면 수정 시각이 늦은 쪽이 이김(copy2로 시각을 같이 옮겨 되풀이 안 함), 지우지 않음.
켜기: 서버 PC .env의 POSTAWARD_SHARE_DIR(사내 공유 폴더 쪽 '낙찰후서류' 위치). NAS 쪽은 POSTAWARD_NAS_DIR(기본 \\\\jiilserver\\jiil-webapp\\낙찰후서류).
shortcut: 1분마다 전체 비교 — 공고 폴더가 수백 개로 늘어 느려지면 바뀐 폴더만 보게 바꿀 것. 리빌드로 NAS가 사내 공유 폴더를 직접 보게 되면 이 동기화는 끔.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path

NAS_DIR = Path(r"\\jiilserver\jiil-webapp\낙찰후서류")
GROUPS = ("공사", "물품·용역")  # postaward.GROUPS의 폴더 이름(표준틀 폴더 _표준틀은 안 맞춤)
SLACK = 2.0  # 초 — 공유 폴더마다 시각 정밀도가 달라 이만큼은 같은 시각으로 봄


def _skip(p: Path) -> bool:
    return p.name.startswith(("~$", ".")) or p.name in ("Thumbs.db", "desktop.ini")


def sync_once(nas: Path, share: Path) -> dict:
    """한 번 맞춤 → {'to_share': n, 'to_nas': m, 'errors': k}."""
    done = {"to_share": 0, "to_nas": 0, "errors": 0}
    for group in GROUPS:
        a_root, b_root = nas / group, share / group
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
    return done


class AwardShareSync(threading.Thread):
    def __init__(self, nas: Path, share: Path, every: int = 60):
        super().__init__(daemon=True, name="award-share-sync")
        self.nas, self.share, self.every = nas, share, every
        self.stop = threading.Event()

    @classmethod
    def from_env(cls) -> "AwardShareSync | None":
        share = os.environ.get("POSTAWARD_SHARE_DIR", "").strip()
        if not share:
            return None
        return cls(Path(os.environ.get("POSTAWARD_NAS_DIR", "").strip() or NAS_DIR), Path(share))

    def run(self) -> None:
        while not self.stop.is_set():
            if self.nas.is_dir():
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
    print("ok")


if __name__ == "__main__":
    selfcheck()
