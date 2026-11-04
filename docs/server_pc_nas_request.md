# 서버 PC → NAS 파일 복사 요청

웹앱을 NAS로 옮기기 위한 시험 준비입니다. 서버 PC에 있는 파일을 **복사**만 해 주시면 됩니다.

- **서버 PC의 웹앱은 끄지 말아 주세요.** 시험 기간에도 계속 운영합니다.
- 원본은 옮기거나 지우지 말아 주세요.
- 코드는 NAS에 이미 넣어 두어서 복사하지 않으셔도 됩니다.

## 넣을 곳

```
\jiilserver\jiil-webapp\Negotiate-Contract-Logic-First-try
```

탐색기 주소창에 입력해 여세요. 로그인 창이 뜨면 본인 NAS 계정을 입력하시면 됩니다.

## 복사할 것

서버 PC의 웹앱 폴더(`Negotiate-Contract-Logic-First-try`)에서 복사합니다.

| # | 항목 | 비고 |
|---|---|---|
| 1 | `.env` 파일 | 숨김 파일일 수 있습니다. 탐색기 보기 → 숨긴 항목 체크 |
| 2 | `data` 폴더 전체 | 참가여부·판단 기록 |
| 3 | `output` 폴더 전체 | 과거 개찰 자료 포함 |
| 4 | 구글 서비스 계정 키 파일(.json) | `.env`의 `GOOGLE_SERVICE_ACCOUNT_FILE` 줄에 적힌 위치에 있습니다. NAS의 `data` 폴더 안에 넣어 주세요. 그 줄이 없으면 건너뛰세요 |

## 확인해서 알려 주실 것

값은 적지 말고 **이름만** 알려 주세요.

1. `.env`에 `APP_TOKEN` 줄이 있는지
2. 구글 키 파일을 넣었다면 그 파일 이름
3. 윈도우 환경변수에만 있는 설정이 있는지. Git Bash에서 아래를 실행하면 이름만 나옵니다. 그중 `.env`에 없는 이름을 알려 주세요.

```
env | cut -d= -f1 | grep -E "NARA|APP_TOKEN|GOOGLE|FEEDBACK|BACKUP|JIIL|TELEGRAM|SMTP|ANTHROPIC|BID_|OUTPUT"
```

4. 웹앱이 어떤 명령이나 방법으로 켜져 있는지 (예: `bash webapp/start.sh`)

## 보안 주의

`.env`와 구글 키 파일에는 인증 정보가 들어 있습니다. `data`와 `output`에는 내부 자료가 들어 있습니다.

- 카톡·메일로 보내지 말고 탐색기로 직접 복사해 주세요.
- GitHub에 올리지 말아 주세요.

## 막히면

NAS 폴더가 안 보이거나 "액세스 거부"가 나오면 그 화면을 캡처해서 보내 주세요.
