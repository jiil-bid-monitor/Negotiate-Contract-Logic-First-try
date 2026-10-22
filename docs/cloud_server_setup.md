# 클라우드 서버로 옮기기 — 네이버클라우드 (서버 PC를 꺼도 웹앱이 켜져 있게)

> 서버 PC(지금 웹앱이 켜져 있는 PC) 담당자용 안내입니다. 한 번만 하면 됩니다. 1시간 정도 걸립니다.
> 끝나면 팀원은 **회사 밖에서도** `https://…sslip.io/?t=…` 주소로 웹앱을 쓰고, 서버 PC는 꺼도 됩니다.

## 무엇이 바뀌나

| | 지금 (서버 PC) | 옮긴 뒤 (클라우드 서버) |
|---|---|---|
| 켜져 있는 시간 | 서버 PC가 켜져 있을 때만 | 24시간 (재부팅돼도 자동으로 켜짐) |
| 접속 | 사내망만 `http://192.168.0.22:8765/?t=jiil2026` | 어디서나 `https://<IP>.sslip.io/?t=<새 토큰>` |
| 판단 기록 | 서버 PC `data/` | 서버 `data/` — PC 기록을 그대로 옮김 |
| 피드백 시트 S~V | 서버 PC가 씀 | 서버가 씀 (같은 키·같은 시트) |
| 백업 | 구글 드라이브 폴더 | 서버 안 날짜별 30일 + 가끔 PC로 받아 오기 (아래 "백업") |
| 코드 업데이트 | `Ctrl+C` → `git pull` → `start.sh` | 명령 한 줄 (아래 "업데이트") |

- 주소가 인터넷에 열리므로 **https**(자물쇠)로만 열고, 접속 토큰을 길고 무작위인 새 값으로 바꿉니다. 예전 `jiil2026` 주소는 더 이상 안 씁니다.
- 매일·주간 텔레그램 보고는 원래 GitHub에서 돌아서 바뀌는 것이 없습니다.

## 비용

- **Micro 서버(1GB 메모리·10GB 디스크)는 서버 비용이 1년 무료**입니다 (결제 정보를 등록한 달부터).
- **공인 IP**는 무료가 아니라 따로 요금이 나옵니다. 가입할 때 [요금 안내](https://www.ncloud.com/v2/product/compute/server)에서 확인하세요.
- **1년이 지나면 반납하지 않는 한 자동으로 요금이 나갑니다**(월 1만 원 안팎 — 2025년 기준이라 확인 필요). 달력에 적어 두세요.

## 준비물

- 회사 결제 수단 (네이버클라우드 가입·결제 등록용)
- 서버 PC — 지금 웹앱이 켜지는 PC. 판단 기록·키 파일·`.env`가 거기 있습니다.

## 1. 네이버클라우드 가입

1. https://www.ncloud.com 에서 회원가입(회사면 **사업자 회원**) → 로그인 → **마이페이지 → 결제 수단 등록**.
2. 오른쪽 위 **콘솔**로 들어갑니다. 화면 위쪽 리전이 **Korea**, 플랫폼이 **VPC**인지 확인합니다.

> 메뉴 이름·위치는 네이버클라우드가 바꿀 수 있습니다. 아래 이름과 조금 달라도 같은 뜻의 메뉴를 고르면 됩니다.

## 2. 네트워크(VPC·서브넷) 만들기

서버가 들어갈 네트워크입니다. 한 번만 만듭니다.

1. **Services → Networking → VPC → VPC Management → VPC 생성**
   - 이름 `jiil-vpc`, IP 주소 범위 `10.0.0.0/16` → 생성 (1~2분 뒤 "운영중")
2. **Subnet Management → Subnet 생성**
   - 이름 `jiil-subnet`, VPC `jiil-vpc`, IP 주소 범위 `10.0.1.0/24`, 가용 Zone 아무거나(KR-2 등)
   - Network ACL: 기본값, **Internet Gateway 전용 여부: Y (Public)**, 용도: 일반 → 생성

## 3. 서버 만들기

**Services → Compute → Server → 서버 생성**

| 항목 | 고를 것 |
|---|---|
| 서버 이미지 | **Ubuntu** — 22.04 또는 24.04 (Micro에서 20.04만 보이면 그것도 됩니다) |
| VPC / Subnet | `jiil-vpc` / `jiil-subnet` |
| 서버 타입 | **Micro** (mi1, 1년 무료) |
| 서버 이름 | `jiil-webapp` |
| 공인 IP | **새로운 공인 IP 할당** (이 화면에 없으면 4번에서 따로 붙임) |
| 인증키 | **새로운 인증키 생성** → 이름 `jiil-server` → `.pem` 파일 내려받기 |
| 네트워크 접근 설정(ACG) | 기본 ACG 그대로 (5번에서 규칙을 넣음) |

- 내려받은 `jiil-server.pem`은 서버 관리자 비밀번호를 볼 때 필요합니다. 키 파일처럼 **레포 밖** `jiil-keys` 폴더에 둡니다.
- 서버 생성 → 몇 분 뒤 상태가 **운영중**.

## 4. 공인 IP 확인

서버 목록에서 `jiil-webapp`의 **공인 IP**(예: `110.165.1.2`)를 적어 둡니다.
비어 있으면 **Server → Public IP → 공인 IP 신청** → 적용 서버 `jiil-webapp`.

## 5. 방화벽(ACG) 열기

**Server → ACG** → `jiil-vpc`의 기본 ACG 선택 → **ACG 설정** → Inbound 규칙 3개 추가 → 적용

| 프로토콜 | 접근 소스 | 허용 포트 | 뜻 |
|---|---|---|---|
| TCP | **myIp** 버튼(지금 PC의 IP) | 22 | 서버 관리(설치·업데이트) — 사무실에서만 |
| TCP | `0.0.0.0/0` | 80 | https 인증서 발급·주소 넘김 |
| TCP | `0.0.0.0/0` | 443 | 팀원 접속 (https) |

- 22번은 **사무실 PC에서만** 열어 둡니다. 집에서 업데이트하려면 그때 그 IP를 하나 더 넣으면 됩니다.
- 웹앱 포트 8765는 열지 않습니다 (서버 안에서만 쓰고, 바깥은 443이 받습니다).

## 6. 관리자 비밀번호 보기

서버 목록에서 `jiil-webapp` 선택 → **서버 관리 및 설정 변경 → 관리자 비밀번호 확인** → `jiil-server.pem` 첨부 → 비밀번호 표시.
이 비밀번호를 7번에서 두 번 입력합니다(사용자 이름은 `root`).

## 7. 서버 PC에서 옮기기 (명령 한 줄)

서버 PC의 Git Bash에서:

1. 웹앱 창에서 **`Ctrl + C`로 끕니다.** 켜 둔 채로는 스크립트가 멈춥니다(옮긴 뒤 누른 판단이 빠지고, 두 서버가 같은 시트에 쓰게 되므로).
2. 레포 폴더로 가서 최신 코드를 받고 옮기기를 실행합니다.
   ```bash
   cd "/c/Users/HJE/나라장터 수집/Negotiate-Contract-Logic-First-try"
   git pull
   bash deploy/upload_from_pc.sh 110.165.1.2      # ← 4번에서 적은 공인 IP
   ```
3. `root@110.165.1.2's password:`가 나오면 6번 비밀번호를 붙여 넣고 Enter (화면엔 안 보임). **두 번** 묻습니다.
4. 5~10분 뒤 마지막에 아래가 나오면 끝입니다.
   ```
   설치 끝. 팀원에게 줄 주소 (어디서나 열림):
       https://110-165-1-2.sslip.io/?t=…
   ```
   "https 확인이 아직입니다"가 나오면 5번 80·443 규칙을 확인하고 1~2분 뒤 그 주소를 열어 보세요(인증서 발급에 잠깐 걸릴 수 있음).

스크립트가 하는 일: 이 PC의 판단 기록(`data/participation.json`·`promoted.json`·`webapp.sqlite3`·`doc_products.json`), 과거 실적 폴더
(`jiil-past-contracts`), 구글 키(`.env`의 `GOOGLE_SERVICE_ACCOUNT_FILE`), 인증키(`NARA_SERVICE_KEY`)·피드백 시트 ID를 묶어 올리고
→ 서버에서 파이썬·웹앱·https(Caddy)·자동 실행을 설치합니다. 서버 `.env`에는 새 접속 토큰이 들어갑니다.

## 8. 확인하고 팀에 알리기

1. 그 주소를 열어 공고·판단 기록이 PC 때와 같은지 봅니다. 판단을 하나 눌러 1분 뒤 피드백 시트 S열에 들어오는지 봅니다.
2. 팀원에게 새 주소를 알립니다. 한 번 열면 그 브라우저는 1년 동안 토큰 없이도 열립니다. **주소는 비밀번호와 같으니 팀 밖에 공유하지 마세요.**
3. **서버 PC 웹앱은 이제 켜지 않습니다.** (켜면 판단 기록이 서버와 갈라지고, 두 곳이 같은 시트에 씁니다.)

## 업데이트 (코드가 바뀌었을 때)

예전의 "`Ctrl+C` → `git pull` → `bash webapp/start.sh`" 대신, 사무실 PC의 Git Bash에서:
```bash
ssh root@110.165.1.2 bash /home/jiil/negotiate-contract-logic-first-try/deploy/update_server.sh
```
`업데이트 끝: …`이 나오면 팀원은 F5. 판단 기록·토큰은 그대로입니다.

## 백업

- 서버는 판단을 저장할 때마다 `/home/jiil/backup`에 최신본과 날짜별 사본(30일)을 남깁니다. 판단은 피드백 시트 S~V에도 들어갑니다.
- 서버가 통째로 망가지는 경우에 대비해 **가끔(주 1회쯤) PC로 받아 둡니다**:
  ```bash
  bash deploy/backup_from_server.sh 110.165.1.2
  ```
  PC `.env`의 `BACKUP_DIR`(구글 드라이브 폴더) 아래 `클라우드서버` 폴더로 받습니다.
- 서버를 새로 만들어야 하면: 받아 둔 `participation.json`·`promoted.json`을 서버 PC `data/`에 넣고 7번을 다시 하면 됩니다.

## 서버 관리 명령 (필요할 때만)

`ssh root@<공인IP>`로 들어가서:

| 하고 싶은 것 | 명령 |
|---|---|
| 웹앱 상태 | `systemctl status jiil-webapp` |
| 웹앱 로그 (예전 서버 창) | `journalctl -u jiil-webapp -n 50` (계속 보기: `-f`, 끝내기 `Ctrl+C`) |
| 웹앱 다시 켜기 | `systemctl restart jiil-webapp` |
| 설정 고치기 (`.env`) | `nano /home/jiil/negotiate-contract-logic-first-try/.env` → 저장 후 다시 켜기 |
| 접속 주소(토큰) 다시 보기 | `grep APP_TOKEN /home/jiil/negotiate-contract-logic-first-try/.env` |

접속 토큰을 바꾸려면(주소가 밖으로 샜을 때) `.env`의 `APP_TOKEN` 값을 바꾸고 다시 켭니다. 팀원은 새 주소로 다시 들어가야 합니다.

## 안 될 때

| 나온 것 | 해결 |
|---|---|
| `이 PC 웹앱이 아직 켜져 있습니다` | 웹앱 창에서 `Ctrl+C` 후 다시 실행 |
| `Connection timed out` (비밀번호 묻기 전) | 5번 ACG에 22번 규칙(접근 소스 = 지금 PC IP)이 있는지, 공인 IP가 맞는지 |
| `Permission denied` | 6번 비밀번호를 다시 확인 (사용자는 `root`) |
| `https 확인이 아직입니다` | ACG 80·443 규칙 확인, 1~2분 뒤 주소 열기. 계속 안 되면 서버에서 `journalctl -u caddy -n 30` |
| `웹앱이 켜지지 않았습니다` + 로그 | 로그를 그대로 개발 담당(Claude 세션)에 보내기 |
| 주소는 열리는데 "접속 토큰이 필요합니다" | 주소 끝 `?t=…`까지 붙여서 열기 |

## 개발자용

- 스크립트: `deploy/upload_from_pc.sh`(PC → 서버), `deploy/setup_server.sh`(서버 설치, 다시 돌려도 판단 기록·`.env` 유지 — PC 기록으로 덮으려면 `FORCE_DATA=1`),
  `deploy/update_server.sh`, `deploy/backup_from_server.sh`.
- 서버 배치: 사용자 `jiil`, 레포 `/home/jiil/negotiate-contract-logic-first-try`(깊이 50 클론 — `webapp/reportsync.py`가 bot-state를 fetch),
  과거 실적 `/home/jiil/jiil-past-contracts`(레포 옆이라 `JIIL_REPO` 불필요), 가상환경 `/home/jiil/venv`, systemd `jiil-webapp`
  (`EnvironmentFile=.env`, `Restart=always`), Caddy가 `<IP 점→대시>.sslip.io`로 Let's Encrypt 인증서를 받아 `127.0.0.1:8765`로 넘김.
- `.env`의 `COOKIE_SECURE=1` → 토큰 쿠키에 `Secure`(https로만 보냄). 사내 http 서버 PC에선 넣지 말 것(쿠키가 안 저장됨).
