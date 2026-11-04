# 서버 PC → NAS 파일 복사 요청

웹앱을 NAS로 옮기기 위한 시험 준비입니다. 서버 PC에 있는 파일을 **복사**만 해 주시면 됩니다.

- **서버 PC의 웹앱은 끄지 말아 주세요.** 시험 기간에도 계속 운영합니다.
- 원본은 옮기거나 지우지 말아 주세요.
- 코드는 NAS에 이미 넣어 두어서 복사하지 않으셔도 됩니다.

## 넣을 곳

```
\\jiilserver\jiil-webapp\Negotiate-Contract-Logic-First-try
```

탐색기 주소창에 입력해 여세요(맨 앞 `\\` 두 개까지 그대로). 로그인 창이 뜨면 본인 NAS 계정을 입력하시면 됩니다.

## 복사할 것

서버 PC의 웹앱 폴더(`Negotiate-Contract-Logic-First-try`)에서 아래 항목을 복사해, 위 NAS 폴더 바로 아래에 붙여넣어 주세요.

### 1. `.env` 파일

서비스키, 접속 토큰 등 운영 설정이 들어 있는 파일입니다.

1. 웹앱 폴더 맨 위(`webapp` 폴더, `requirements.txt`와 같은 위치)에서 `.env`를 찾습니다.
2. 안 보이면 탐색기 상단 **보기** → **숨긴 항목**에 체크합니다.
3. 복사해서 NAS 폴더 바로 아래에 붙여넣습니다.

내용은 고치지 말아 주세요. NAS에 맞게 고칠 부분은 복사가 끝난 뒤 이쪽에서 고치겠습니다.

### 2. `data` 폴더 전체

참가여부, 판단 기록, 제외에서 올린 공고 기록 등이 들어 있습니다.

- 폴더째로 복사합니다. 일부 파일만 고르지 말아 주세요.
- 대표적으로 `participation.json`, `promoted.json`, `webapp.sqlite3`가 들어 있습니다. 서버 PC 상황에 따라 없는 파일이 있어도 괜찮습니다.

### 3. `output` 폴더 전체

투찰 계산기의 추천 칸이 쓰는 과거 개찰 자료와 수집 결과가 들어 있습니다.

- 폴더째로 복사합니다.
- `bid_results*.jsonl`, `bid_history_*.json` 파일이 있는지 봐 주세요. 없으면 추천 칸이 비어 나오므로, 없을 때는 알려 주세요.
- 파일이 많으면 복사에 시간이 걸릴 수 있습니다.

### 4. 구글 서비스 계정 키 파일 (.json)

판단 기록을 구글 시트에 쓸 때 쓰는 키입니다. 먼저 `.env`를 메모장으로 열어 확인합니다.

- **`GOOGLE_SERVICE_ACCOUNT_FILE=` 줄이 있을 때:** 그 줄에 적힌 경로의 파일을 NAS의 `data` 폴더 안에 복사합니다.
- **`GOOGLE_SERVICE_ACCOUNT_JSON=` 줄만 있을 때:** 키가 `.env` 안에 들어 있으니 따로 복사하지 않습니다.
- **둘 다 없을 때:** 건너뜁니다.

메모장을 닫을 때 저장하지 말아 주세요.

### 5. `jiil-quant-docs` 폴더 전체 (정량평가서 탭용)

정량평가서 탭이 쓰는 **회사 자료 잠금 묶음**(`vault\company.enc`)과 옛 한글 변환기가 들어 있는 폴더입니다.

- 서버 PC에서 웹앱 폴더와 **나란히** 있습니다: `C:\Users\HJE\나라장터 수집\jiil-quant-docs`
- 폴더째로 복사해, 웹앱 폴더 **안이 아니라 옆**에 붙여넣습니다:
  `\\jiilserver\jiil-webapp\jiil-quant-docs`
- 묶음 비밀번호(`QUANT_VAULT_PASSPHRASE`)는 1번 `.env`에 들어 있어 따로 할 일은 없습니다.
- `.env`의 옛 한글 변환기 설정(`HWP2HWPX_CMD`)은 윈도우 경로라 NAS에선 그대로 안 됩니다 — 이쪽에서 고칩니다.
  그 전까지 NAS 정량평가서 탭은 HWPX 공고만 서식을 자릅니다(옛 한글 .hwp 공고는 경고만).

### 복사 후 모습

```
\\jiilserver\jiil-webapp\Negotiate-Contract-Logic-First-try
  ├ .env
  ├ data\                (구글 키 파일이 있으면 이 안에)
  ├ output\
  ├ webapp\              (이미 있음)
  └ docker-compose.yml   (이미 있음)

\\jiilserver\jiil-webapp\jiil-quant-docs      (5번 — 웹앱 폴더 옆)
  └ vault\company.enc
```

같은 이름의 파일을 덮어쓸지 묻는 창이 뜨면 진행하지 말고 알려 주세요.

## 확인해서 알려 주실 것

값은 적지 말고 **이름만** 알려 주세요.

1. `.env`에 `APP_TOKEN` 줄이 있는지
2. 구글 키 파일을 넣었다면 그 파일 이름
3. 윈도우 환경변수에만 있는 설정이 있는지. Git Bash에서 아래를 실행하면 이름만 나옵니다. 그중 `.env`에 없는 이름을 알려 주세요.

```
env | cut -d= -f1 | grep -E "NARA|APP_TOKEN|GOOGLE|FEEDBACK|BACKUP|JIIL|TELEGRAM|SMTP|ANTHROPIC|BID_|OUTPUT|QUANT|HWP2HWPX"
```

4. 웹앱이 어떤 명령이나 방법으로 켜져 있는지 (예: `bash webapp/start.sh`)
5. NAS에 `jiil-quant-docs\vault\company.enc`가 들어갔는지

## 보안 주의

`.env`와 구글 키 파일에는 인증 정보가 들어 있습니다. `data`와 `output`에는 내부 자료, `jiil-quant-docs`에는 회사 서류(잠금 묶음)가 들어 있습니다.

- 카톡·메일로 보내지 말고 탐색기로 직접 복사해 주세요.
- GitHub에 올리지 말아 주세요.

## 막히면

NAS 폴더가 안 보이거나 "액세스 거부"가 나오면 그 화면을 캡처해서 보내 주세요.
