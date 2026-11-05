"""지일 회사 자료 잠금 묶음 — 원본은 저장소에 올리지 않고, 암호화한 파일 하나(vault/company.enc)만 비공개 저장소에 둔다.

(2026-11-03 사용자 결정: 웹앱이 GitHub에서 지일 자료를 가져오되 암호화해 유출 방지. 10/30 '저장·공개 금지' 원칙의 예외 —
잠근 묶음만, 비밀번호는 서버 PC 환경변수에만.)

    # 자료가 바뀌었을 때(담당 PC) — 폴더를 잠가 vault/company.enc를 만들고 커밋
    QUANT_VAULT_PASSPHRASE=… python -m quantdocs vault seal --src "<회사자료 폴더>"
    # 들어 있는 파일 목록만 보기(내용은 출력 안 함)
    QUANT_VAULT_PASSPHRASE=… python -m quantdocs vault list

회사자료 폴더 모양:
    회사자료_양식.xlsx   회사정보·연혁·서류상태·실적목록 시트(빈 양식은 `python -m quantdocs templates`)
    files/…              스캔 PDF 등 — 양식의 '파일 이름' 칸은 이 폴더 안 경로

형식: b"JQDV1" | salt(16) | iv(12) | AES-256-GCM(gzip(tar)) + tag(16) — 키는 scrypt(N=2^15, r=8, p=1).
비밀번호가 틀리거나 파일이 바뀌면 GCM 검사에서 바로 실패한다. 풀 때는 **메모리에서만** — 디스크에 원본을 남기지 않는다.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEALED = ROOT / "vault" / "company.enc"  # 이 저장소엔 두지 않음(.gitignore) — 아래 sealed_path()가 찾는 순서의 하나


def sealed_path() -> Path:
    """잠금 묶음 위치: QUANT_VAULT_PATH(.env 또는 환경변수) → 옆 폴더의 비공개 레포 jiil-quant-docs/vault/company.enc → 이 저장소 vault/.
    (11/03 프로젝트 1 레포로 합칠 때 — 공개 레포라 회사 자료 묶음은 비공개 레포에 그대로 두고 여기서 읽음)"""
    given = read_env("QUANT_VAULT_PATH")
    if given:
        return Path(given)
    sibling = ROOT.parent / "jiil-quant-docs" / "vault" / "company.enc"
    if SEALED.exists() or not sibling.exists():
        return SEALED
    return sibling
MAGIC = b"JQDV1"
ENV = "QUANT_VAULT_PASSPHRASE"
INDEX = "회사자료_양식.xlsx"
ENV_FILE = ROOT / ".env"


class VaultError(Exception):
    pass


def read_env(key: str) -> str:
    """설정값: 저장소 .env의 값을 글자 그대로(우선), 없으면 환경변수.

    .env를 bash가 읽으면 '$'·따옴표·공백이 바뀌고, 윈도우 메모장으로 고치면 줄 끝에 '\r'이 붙어
    잠글 때와 열 때 비밀번호가 달라질 수 있어 파일에서 직접 읽는다. 바깥 작은따옴표 한 겹은 뗌.
    프로젝트 1 웹앱(webapp)은 .env를 읽지 않아도 이 함수로 같은 값을 씀."""
    env = ENV_FILE
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.startswith(key + "="):
                value = line[len(key) + 1:].strip("\r")
                if len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
                if value:
                    return value
    return os.environ.get(key, "").strip("\r")


def read_passphrase() -> str:
    return read_env(ENV)


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=1 << 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def seal_bytes(plain: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(passphrase) < 12:
        raise VaultError("비밀번호는 12자 이상으로 해 주세요")
    salt, iv = os.urandom(16), os.urandom(12)
    return MAGIC + salt + iv + AESGCM(_key(passphrase, salt)).encrypt(iv, gzip.compress(plain), MAGIC)


def unseal_bytes(data: bytes, passphrase: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not data.startswith(MAGIC):
        raise VaultError("회사 자료 묶음 형식이 아닙니다")
    body = data[len(MAGIC):]
    try:
        return gzip.decompress(AESGCM(_key(passphrase, body[:16])).decrypt(body[16:28], body[28:], MAGIC))
    except InvalidTag as err:
        raise VaultError("비밀번호가 틀리거나 묶음 파일이 손상됐습니다") from err


def _tar(src: Path) -> bytes:
    src = Path(src)
    if not (src / INDEX).exists():
        raise VaultError(f"{src} 안에 {INDEX}가 없습니다 — `python -m quantdocs templates`로 만든 양식을 채워 넣으세요")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.startswith(("~$", ".")):
                info = tar.gettarinfo(str(p), arcname=p.relative_to(src).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with p.open("rb") as fh:
                    tar.addfile(info, fh)
    return buf.getvalue()


def seal_folder(src: Path, passphrase: str, out: Path | None = None) -> Path:
    out = Path(out) if out is not None else sealed_path()
    data = seal_bytes(_tar(src), passphrase)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out


@dataclass
class Vault:
    """메모리에 푼 회사 자료. files: 묶음 안 경로 → 내용."""

    files: dict[str, bytes]
    info: dict[str, str] = field(default_factory=dict)  # 회사정보 시트(항목 → 값)
    history: list[dict] = field(default_factory=list)  # 연혁
    docs: list[dict] = field(default_factory=list)  # 서류상태
    tracks: list[dict] = field(default_factory=list)  # 실적목록
    staff: list[dict] = field(default_factory=list)  # 인력목록(자기평점 기술인력)
    patents: list[dict] | None = None  # 특허목록(자기평점 보유기술) — 시트가 없으면 None(자료 없음)
    loose: list[str] = field(default_factory=list)  # 묶음 밖에서 더 읽은 PDF(파일 이름)
    staff_src: str = ""  # 인력목록을 묶음 밖 엑셀에서 읽었으면 그 파일 이름
    patent_src: str = ""  # 특허 건수를 특허증 파일로 셌으면 '파일'(특허목록 시트가 비었을 때)

    def file(self, name: str | None) -> bytes | None:
        """양식의 '파일 이름' 칸 값으로 파일 찾기 — 'files/' 붙여도·안 붙여도, 경로 없이 이름만 적어도."""
        if not name:
            return None
        name = str(name).strip().replace("\\", "/")
        for cand in (name, f"files/{name}"):
            if cand in self.files:
                return self.files[cand]
        tail = [k for k in self.files if k.rsplit("/", 1)[-1] == name.rsplit("/", 1)[-1]]
        return self.files[tail[0]] if len(tail) == 1 else None

    def held_codes(self) -> set[str]:
        """서류상태의 업종코드·세부품명번호(상태가 '재발급'인 만료 서류는 뺌)."""
        codes: set[str] = set()
        for d in self.docs:
            if str(d.get("상태") or "").strip() == "재발급":
                continue
            for c in str(d.get("업종코드·세부품명번호") or "").replace("/", ",").split(","):
                c = "".join(ch for ch in c if ch.isdigit())
                if len(c) in (4, 8, 10):
                    codes.add(c)
        return codes

    def docs_for_code(self, code: str) -> list[dict]:
        return [d for d in self.docs if code in "".join(ch if ch.isdigit() else " " for ch in
                                                      str(d.get("업종코드·세부품명번호") or "")).split()]


def _rows(ws) -> list[dict]:
    it = ws.iter_rows(values_only=True)
    try:
        head = [str(h or "").strip() for h in next(it)]
    except StopIteration:
        return []
    return [dict(zip(head, r)) for r in it if any(v not in (None, "") for v in r)]


def load(passphrase: str | None = None, path: Path | None = None) -> Vault:
    from openpyxl import load_workbook

    path = Path(path) if path is not None else sealed_path()
    passphrase = passphrase if passphrase is not None else read_passphrase()
    if not passphrase:
        raise VaultError(f"비밀번호가 없습니다 — 서버 PC 환경변수 {ENV}를 설정하세요")
    if not Path(path).exists():
        raise VaultError(f"회사 자료 묶음이 없습니다: {path} — 담당 PC에서 `python -m quantdocs vault seal` 후 커밋·pull")
    plain = unseal_bytes(Path(path).read_bytes(), passphrase)
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r") as tar:
        for m in tar.getmembers():
            if m.isfile() and not m.name.startswith(("/", "..")) and ".." not in Path(m.name).parts:
                files[m.name] = tar.extractfile(m).read()
    if INDEX not in files:
        raise VaultError(f"묶음 안에 {INDEX}가 없습니다")
    wb = load_workbook(io.BytesIO(files[INDEX]), data_only=True)
    v = Vault(files)
    if "회사정보" in wb.sheetnames:
        v.info = {str(r.get("항목") or "").strip(): str(r.get("값")).strip()
                  for r in _rows(wb["회사정보"]) if r.get("항목") and r.get("값") not in (None, "")}
    for sheet, attr in (("연혁", "history"), ("서류상태", "docs"), ("실적목록", "tracks"), ("인력목록", "staff"),
                        ("특허목록", "patents")):
        if sheet in wb.sheetnames:
            setattr(v, attr, _rows(wb[sheet]))
    add_loose(v)
    derive_patents(v)
    return v


def loose_dir() -> Path | None:
    """묶음 밖 회사 서류 PDF를 둘 수 있는 폴더 — .env QUANT_LOOSE_DIR, 없으면 옆의 비공개 레포 jiil-quant-docs 맨 위
    (11/04 사용자 요청: 'jiil-quant-docs에 있는 경쟁입찰참가등록증.pdf를 읽어야 해'). 그 폴더 바로 아래 PDF만(하위 폴더는 안 봄)."""
    given = read_env("QUANT_LOOSE_DIR")
    d = Path(given) if given else ROOT.parent / "jiil-quant-docs"
    return d if d.is_dir() else None


_STAFF_GRADE = ("등급", "기술등급", "기술자등급", "업무")  # 받은 기술인력표는 등급이 '업무' 칸에 있었음(11/05)


def read_staff_xlsx(data: bytes) -> list[dict] | None:
    """기술인력 엑셀(머리 줄에 '이름' + '입사…' + 등급 칸) → 인력목록 줄. 그런 시트가 없으면 None.
    머리는 앞 5줄 안에서 찾음, '재직' 칸이 없으면 모두 재직."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    except Exception:  # noqa: BLE001 — 엑셀이 아니거나 깨진 파일
        return None
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        for hi, head in enumerate(rows[:5]):
            names = [re.sub(r"\s+", "", str(h or "")) for h in head]
            col = lambda *ws_: next((i for i, h in enumerate(names) if h in ws_), None)
            c_name = col("이름", "성명")
            c_join = next((i for i, h in enumerate(names) if h.startswith("입사")), None)
            c_grade = col(*_STAFF_GRADE)
            if c_name is None or c_join is None or c_grade is None:
                continue
            c_emp, c_lic = col("재직", "재직여부", "상태"), col("자격증", "자격")
            out = []
            for r in rows[hi + 1:]:
                get = lambda i: r[i] if i is not None and i < len(r) else None
                if not str(get(c_name) or "").strip():
                    continue
                out.append({"이름": str(get(c_name)).strip(), "입사일": get(c_join), "재직": str(get(c_emp) or "재직").strip(),
                            "등급": str(get(c_grade) or "").strip(), "자격증": get(c_lic)})
            return out
    return None


# jiil-quant-docs 폴더 안에서 안 보는 하위 폴더(프로그램·저장소 폴더)
_SKIP_DIRS = {"config", "data", "docs", "quantdocs", "tests", "tools", "vault", "node_modules", "__pycache__", "venv", ".venv"}
LOOSE_EXTS = (".pdf", ".hwp", ".hwpx", ".jpg", ".jpeg", ".png")


def _loose_files(folder: Path, depth: int = 4) -> list[Path]:
    """폴더와 하위 폴더(11/05 사용자: '정량서류'·'회사자료' 폴더에 모든 자료)의 파일 — 프로그램 폴더·숨김 폴더는 빼고, 4단계까지."""
    out: list[Path] = []
    try:
        items = sorted(folder.iterdir())
    except OSError:
        return out
    for x in items:
        if x.name.startswith((".", "~$")):
            continue
        if x.is_dir():
            if depth > 0 and x.name not in _SKIP_DIRS:
                out += _loose_files(x, depth - 1)
        elif x.is_file():
            out.append(x)
    return out


def add_loose(v: Vault, folder: Path | None = None) -> None:
    """그 폴더(와 하위 폴더)의 PDF·한글·그림을 묶음 files/에 있는 것처럼 메모리에 더함(같은 이름이 묶음에 있으면 묶음 것을 씀),
    기술인력 엑셀은 인력목록으로 — 서식 채우기·통합본·자기평점 모두 씀."""
    folder = folder or loose_dir()
    if folder is None:
        return
    found = _loose_files(folder)
    have = {k.rsplit("/", 1)[-1] for k in v.files}
    for p in found:
        if p.suffix.lower() not in LOOSE_EXTS or p.name in have:
            continue
        rel = p.relative_to(folder).as_posix()
        try:
            v.files[f"files/{rel}"] = p.read_bytes()
        except OSError:
            continue
        v.loose.append(rel)
        have.add(p.name)
    # 기술인력 엑셀 — 폴더에 두면 묶음을 다시 잠그지 않아도 이것을 씀(11/05 사용자 요청), 여럿이면 가장 최근에 고친 파일
    xlsx = [p for p in found if p.suffix.lower() == ".xlsx"]
    for p in sorted(xlsx, key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            staff = read_staff_xlsx(p.read_bytes())
        except OSError:
            continue
        if staff:
            v.staff, v.staff_src = staff, p.relative_to(folder).as_posix()
            break
    derive_patents(v)


def derive_patents(v: Vault) -> None:
    """특허목록 시트가 비었으면 특허증 파일 수로 특허 건수(파일 하나 = 1건)."""
    if not v.patents:
        found = patent_files(v)
        if found:
            v.patents = [{"명칭": Path(k).stem, "등록번호": Path(k).stem, "파일": k} for k in found]
            v.patent_src = "파일"


PATENT_EXTS = (".pdf", ".jpg", ".jpeg", ".png")


def patent_files(v: Vault) -> list[str]:
    """묶음·폴더 안 특허증 파일(경로나 이름에 특허·실용신안이 든 PDF·그림) — 통합본 증빙·건수 셈."""
    return sorted(k for k in v.files if k.startswith("files/") and k.lower().endswith(PATENT_EXTS)
                  and re.search(r"특허|실용\s*신안", k))
