> **11/03 프로젝트 1 레포(negotiate-contract-logic-first-try)로 합침** — 웹앱 '정량평가서' 탭. 잠금 묶음 `vault/company.enc`는 비공개 레포 jiil-quant-docs에 그대로 두고 옆 폴더에서 읽음(`quantdocs.vault.sealed_path`). 아래 명령은 이 레포 폴더에서 실행.

# 정량평가서 서류 준비 (프로젝트 2)

입찰·협상 공고마다 정량평가서에 낼 **회사 서류를 골라 모으고 양식을 채워** 담당자가 바로 검토·제출할 수 있게 준비한다.

- **범위**: 제출 서류 취합·작성까지. **평가 점수 계산·예측은 하지 않는다.**
- **대상**: 담당자가 **참가하기로 정한 공고 중 정량 서류를 내는 공고 전반** — 협상·적격심사·사업수행능력평가(PQ)·종합심사·
  규격가격동시입찰(11/03 넓힘). 공고 유형을 자동으로 가려 제출 시점·서식 위치·기본 준비 서류를 안내한다(`config/notice_kinds.json`).
  프로젝트 1의 트랙 1(1억 미만 공고를 수집 단계에서 NAS 폴더로 — 참가 판단용)과는 **별개**다.
- **보안**: 회사 서류·실적·인력 자료는 이 저장소에 올리지 않는다(외부 저장·공개 금지).
  저장소에는 코드와 서류 목록(설정)만 둔다. 실제 자료는 사내 PC·NAS 폴더에서만 읽는다(`.gitignore`가 문서·엑셀 파일을 막음).
- 프로젝트 1(나라장터 수집기, `jiil-bid-monitor/Negotiate-Contract-Logic-First-try`)과
  공고 읽기 코드(`nego` 패키지 — 가져다 쓰기만)와 회사 서류 폴더 **'회사서류_최신'만 같이 쓴다**.

## 다루는 서류

`config/documents.json`이 서류 목록의 원본이다(아래 표는 요약).

| # | 서류 | 공고마다 | 자동화 | 사람이 할 일 |
|---|---|---|---|---|
| 1 | 회사 연혁 + 증빙 | 고정 | 가능 | 연혁 변경 시 갱신 |
| 2 | 공고가 요구하는 자격 서류 | 다름 | 가능(요구 자격에 맞는 것만) | — |
| 3 | 실적 증빙 + 실적집계표 | 다름 | 일부(후보 사업 골라 정리) | 제출할 실적 확정 |
| 4 | 인력 정보(자격증) | 자격증 고정, 재직·유효기간 바뀜, PM 직무는 공고마다 | 일부(자격증 첨부) | PM·담당 직무 지정, 재직·유효기간 갱신 |
| 5 | 직접생산확인증명서 | 요구할 때만 | 가능 | — |
| 6 | 경영상태(신용평가등급확인서) | 유효기간 안 고정 | 가능(유효기간 확인) | 지나면 재발급 |
| 7 | 가점·인증 서류 | 요구·배점 있을 때만 | 필요할 때 자동 첨부 | 인증 갱신 시 교체 |
| 8 | 재발급 최신화 자료(실적증명서 등) | 공고마다 | 재발급 요청 목록까지 | 재발급 요청 |

## 지금 있는 것 (1단계 — 뼈대)

```bash
python -m quantdocs list                       # 서류 목록과 자동화 범위
python -m quantdocs check --dir "<회사서류 폴더>"   # 폴더에 서류별 파일이 있는지 (파일 이름 규칙으로)
python -m quantdocs templates --out "<폴더>"       # 담당자가 채울 빈 엑셀 양식(회사자료_양식.xlsx)
python -m quantdocs notice 공고문.hwpx 제안요청서.hwpx --out "<폴더>" --company 회사자료_양식.xlsx --notice-date 2026-08-20
                                                 # 공고 자료 → 공고별_준비표.xlsx (--cut-forms: 서식도 자름)
python -m quantdocs forms 제안요청서.hwpx --out "<폴더>"   # 첨부 서식([서식 N]·〔서식 N〕)을 서식별 HWPX 파일로
```

서식 목록은 공고마다 적는 방식이 달라 네 가지를 모두 읽는다 — 제출도서 표 한 줄("일반현황 및 연혁 | 서식14"),
칸이 줄마다 풀린 표(옛 .hwp: "- 제안사 연혁" 다음 줄 "서식12", "서식28" 다음 줄 "~31"), 목록 줄("3) 참여전문기술자 보유현황(서식 9)"),
서식 목차 표("[서식 4-1] | 공동수급표준협정서(공동이행)"). 정량적 평가서 묶음 안의 서식엔 표시(○).

서식 자르기는 문단 단위 — 서식 제목 문단부터 다음 서식 제목 전까지를 원본 표·글꼴·쪽 설정 그대로 새 HWPX로 만든다.
옛 한글(.hwp)은 먼저 한글에서 'HWPX로 저장'(또는 hwp2hwpx)한 뒤 자른다.

`notice`는 공고 자료(hwp·hwpx·pdf)를 읽어 `공고별_준비표.xlsx`를 만든다 — 시트: 공고정보(마감) · 서식목록(서식 번호·이름·정량평가서 여부·잘라 낸 파일) ·
제출서류(①② 목록) · 실적요건(제출 요건 / 평가 실적 인정 기준, 기간·금액·건당/누적) · 실적후보(`--company`를 주면 실적목록에서
기간·금액·지분율로 고른 후보 — 누적 기준은 합계만, 충족 판정·점수는 안 함). 각 항목에 서류 종류와 채울 곳(회사자료 양식 시트)을 붙인다.
글 읽기는 프로젝트 1의 `nego` 패키지를 씀 — 옆 폴더에 있거나 `NEGO_REPO` 환경변수로 경로를 준다.

빈 양식은 시트 4개 — 안내 · 실적목록 · 인력목록 · 서류상태. 업무·부가세·재직·상태 등은 드롭다운, 날짜·금액 칸은 형식 검사.
양식 파일은 저장소에 두지 않고 명령으로 만든다(채운 양식이 실수로 올라가지 않게). 같은 이름 파일이 있으면 덮어쓰지 않는다.

`check`는 파일 이름에 서류 이름 낱말(`config/documents.json`의 `filename_keywords`)이 들어 있는지만 본다.
OCR·유효기간 판정·양식 채우기는 다음 단계.

## 다음 단계 (미정 — 담당자와 정할 것)

- 회사 연혁·증빙 고정 묶음의 보관 위치, 자료 갱신 담당
- 실적집계표 양식(공고마다 다른지, 회사 고정인지), 실적 엑셀 ↔ 증빙 PDF 연결 규칙
- 재발급 서류의 유효기간 기준('회사서류_최신'의 구분으로 일부 정해짐)
- 검증용 과거 제출본 3~5건 — 누가, 언제 줄지
- 분야 대응표(담당자 분야 ↔ 수집기 공고 분야)

## 웹앱 (서버 PC) — 공고번호 → 정량평가서 묶음

1. 공고번호 입력 → 나라장터에서 공고·첨부를 받아 **필요 서식·자격(보유 ✓/미보유 ✗)·제출서류·실적 요건·실적 후보**를 화면에
2. **만들기** → 잠금 묶음(`vault/company.enc`)을 메모리에서 풀어 서식을 자르고 회사 정보·연혁·실적을 채우고, 요구 자격에 맞는 회사 서류를 골라 zip
3. **내려받기** — `통합본.pdf`(공고 제출서류·요구 자격에 맞는 회사 서류를 **파일 이름 키워드로** 골라 원본 그대로 이어 붙임, 목차·책갈피 — 규칙 `config/bundle_keywords.json`) · `통합서식.hwpx`(서식 전체를 공고 순서대로 이은 한글 파일 하나 — 채운 서식 + 직접 기입 양식, 11/04부터 서식별 파일은 통합서식을 못 만들 때만 `서식/`·`서식_직접기입/`) · `첨부/`(자격·연혁·실적 증빙) · `공고문/` · `공고별_준비표.xlsx`(자격요건·자동입력·준비 필요 시트) · `읽어보기.txt`

이번 단계 범위: 회사 연혁·실적·보유 자격(직접생산 포함)만 채움. 인력·경영상태·신인도·가점은 준비표 '준비 필요'.

### 회사 자료 넣기 (담당 PC) — 원본은 커밋하지 않음
```bash
# 원본 서류에서 바로 만들기: 공사수주현황 엑셀 → 실적목록, 법인일괄서류 → 서류상태·회사정보(사업자등록증)·연혁,
# 직접생산확인증명서 PDF → 서류상태(세부품명번호·유효기간). 종류·업종코드 표는 config/company_import.json
python -m quantdocs import --out "회사자료" --orders "공사수주현황.xlsx" --corp "법인일괄서류.zip" --direct "직접생산증명서.zip" --registration "경쟁입찰참가자격등록증.pdf"
# (또는 빈 양식에서 손으로: python -m quantdocs templates --out "회사자료")
python -m quantdocs vault seal --src "회사자료"     # 비밀번호를 물음 → vault/company.enc (AES-256-GCM)
git add vault/company.enc && git commit -m "회사 자료 묶음 갱신" && git push
```
서류상태 시트의 **업종코드·세부품명번호** 칸이 공고 요구 자격과 맞추는 기준이다(4990, 6012100201 …). '파일 이름'은 `files/` 안 이름.

### 서버 PC에서 켜기
```bash
cd "<jiil-quant-docs 폴더>" && bash start_web.sh
```
처음 한 번 비밀번호를 물어 `.env`(깃 제외)에 둔다. 주소는 `http://<서버 PC>:8766/?t=<토큰>`(토큰은 `.env`의 APP_TOKEN).
프로젝트 1 폴더(`Negotiate-Contract-Logic-First-try`)가 옆에 있어야 한다(나라장터 인증키·공고 읽기 코드 — 다르면 `.env`에 `NEGO_REPO=`).
`QUANT_GIT_PULL=1`이면 불러올 때마다 이 저장소를 pull해 새 잠금 묶음을 받는다.
옛 한글(.hwp) 서식은 `HWP2HWPX_CMD`(hwp2hwpx 변환 명령, `{src}` `{dst}` 자리)를 설정해야 자른다 — 없으면 목록만 보이고 서식은 못 자름.
설치: 자바 JDK(`winget install EclipseAdoptium.Temurin.17.JDK`) → `bash tools/install_hwp2hwpx.sh [시험할.hwp]`
(공개 라이브러리 hwp2hwpx·hwplib·hwpxlib, Apache-2.0 — 받아서 `tools/hwp2hwpx/build/hwp2hwpx-cli.jar`로 빌드하고 `.env`에 적음).
작업 파일은 서버 `data/jobs/`에 2시간 두고 지운다. **내려받은 zip엔 회사 서류가 들어 있으니 사내에서만.**

## 스캔본 회사 서류 글자 인식(OCR) — 서버 PC 설치 (11/04)

연혁·경쟁입찰참가자격등록증·인감증명서가 스캔 이미지 PDF면 Tesseract로 글자를 읽어 회사정보 빈 칸(전화·팩스·대표자 주민등록번호 앞자리)과
연혁을 채운다(`quantdocs.importer.enrich_vault`). 설치가 안 돼 있으면 읽어보기에 "Tesseract가 없어 읽지 못한 서류"로 알리고 넘어간다.

1. Windows용 Tesseract 설치(UB Mannheim 배포판) — 설치 화면 'Additional language data'에서 **Korean** 체크.
   한 줄로(Git Bash를 **관리자 권한으로** 열고 — winget 자동 설치엔 한국어가 빠져 kor 데이터를 따로 받음):
   `cd "C:\Users\HJE\나라장터 수집\Negotiate-Contract-Logic-First-try" && winget install -e --id UB-Mannheim.TesseractOCR && curl -L -o "/c/Program Files/Tesseract-OCR/tessdata/kor.traineddata" https://github.com/tesseract-ocr/tessdata/raw/main/kor.traineddata && py -m pip install pytesseract pypdfium2 && echo >> .env && echo "TESSERACT_CMD='C:\Program Files\Tesseract-OCR\tesseract.exe'" >> .env && "/c/Program Files/Tesseract-OCR/tesseract.exe" --list-langs`
   — 마지막 줄 목록에 `kor`가 보이면 됨. 확인: `py -m quantdocs vault check`에서 스캔본이 'OCR'로 읽힘.
   관리자 권한이 안 되면(kor 받기에서 Permission denied) 한국어 데이터를 사용자 폴더에 두고 `.env` `TESSDATA_PREFIX`로 알림:
   `cd "C:\Users\HJE\나라장터 수집\Negotiate-Contract-Logic-First-try" && git pull origin claude/quantdocs-merge && mkdir -p "$HOME/tessdata" && cp "/c/Program Files/Tesseract-OCR/tessdata/eng.traineddata" "/c/Program Files/Tesseract-OCR/tessdata/osd.traineddata" "$HOME/tessdata/" && curl -L -o "$HOME/tessdata/kor.traineddata" https://github.com/tesseract-ocr/tessdata/raw/main/kor.traineddata && py -m pip install pytesseract pypdfium2 && echo >> .env && echo "TESSERACT_CMD='C:\Program Files\Tesseract-OCR\tesseract.exe'" >> .env && echo "TESSDATA_PREFIX='$(cygpath -w "$HOME/tessdata")'" >> .env && "/c/Program Files/Tesseract-OCR/tesseract.exe" --tessdata-dir "$HOME/tessdata" --list-langs`
2. `py -m pip install -r requirements.txt` (pytesseract·pypdfium2).
3. 설치 폴더가 PATH에 없으면 저장소 `.env`에 `TESSERACT_CMD='C:\Program Files\Tesseract-OCR\tesseract.exe'`(**작은따옴표 필수** — `webapp/start.sh`가 .env를 bash로 읽어 빈칸에서 끊김).
4. NAS(도커)는 `Dockerfile`에 tesseract-ocr-kor가 들어 있음 — 이미지 다시 빌드(부장님).

- 대표자 주민등록번호는 **앞 6자리만** 남기고 뒷자리는 늘 `*******`로 쓴다(원본 번호는 저장하지 않음). 대리인 줄·휴대폰 칸은 비운다.
- OCR 글은 틀릴 수 있다(예: 숫자 한 자리) — 채운 값은 읽어보기 '스캔본이라 OCR로 읽은 서류'를 보고 원본과 대조.
- 서식에 '해당 사항이 없을 경우 "해당 없음" 기재'가 있으면 빈 표 첫 칸에 '해당 없음'(자동입력 시트에 확인 알림).

## 서식 칸이 비어 나올 때 — 회사정보 점검 (11/04)

    py -m quantdocs vault check

묶음 안 PDF마다 읽은 방법(글/OCR/스캔본이라 안 읽음)과 알아본 서류 종류(등록증·사업자·인감·연혁), 서식에 넣는 회사정보(회사명·대표자·주소·
법인등록번호·전화·팩스·대표자 주민등록번호 앞자리)를 어디서 찾았는지 보여 준다. 전화번호·주민등록번호를 못 찾으면 만들기 때도 읽어보기·준비 필요에
'⚠ … 찾지 못해 비어 있음 — 이유 · 할 일'이 나온다.

## 묶음 밖 회사 서류 PDF (11/04)

잠금 묶음(company.enc)에 다시 넣지 않아도, 서버 PC의 **jiil-quant-docs 폴더 맨 위**(프로젝트 폴더 옆, 예: `C:\Users\HJE\나라장터 수집\jiil-quant-docs`)에
둔 PDF도 묶음 `files/`에 있는 것처럼 읽는다 — 회사정보 빈 칸 채우기(전화·팩스 등)와 통합본.pdf 고르기 모두. 같은 이름이 묶음에 있으면 묶음 것을 쓰고,
하위 폴더(vault/ 등)는 안 본다. 다른 폴더를 쓰려면 `.env`에 `QUANT_LOOSE_DIR=폴더 경로`. 이 PDF들은 암호화되지 않으니 jiil-quant-docs에 커밋하지 말 것.

## 회사 전화·FAX 바로잡기 (11/05)

회사 자료(회사정보 시트·등록증 PDF)의 FAX·전화 번호가 틀렸으면 묶음을 다시 잠그지 않고 서버 PC 저장소 `.env`에 한 줄씩 넣는다 —
`QUANT_FAX=번호`, `QUANT_TEL=번호`. 이 값이 묶음 값보다 먼저 쓰인다. 번호는 공개 저장소(이 레포)에 적지 않는다.

## 기술인력 엑셀 (11/05)

jiil-quant-docs 폴더 맨 위에 기술인력 엑셀(.xlsx)을 두면 묶음을 다시 잠그지 않아도 그것을 인력목록으로 쓴다(묶음 인력목록보다 먼저).
머리 줄에 `이름`·`입사일`·등급 칸(`등급`·`기술등급`·`업무`)이 있으면 되고, `재직`(퇴사면 '퇴사')·`자격증` 칸은 있으면 씀. 입사일은 '2011.09.01'처럼 적어도 됨.
여러 개면 가장 최근에 고친 파일. 직원 이름이 든 파일이라 jiil-quant-docs에 커밋하지 말 것.
정량평가서 화면 '기술인력 (자기평점)' 칸에서 이번 공고 점수에 넣을 사람을 체크(처음엔 공고 조건 — 퇴사·재직 기간·등급 — 에 맞는 사람 모두 체크).

## 특허증 폴더 (11/05)

jiil-quant-docs 폴더 안 이름에 '특허'가 든 하위 폴더(예: `특허증\`)의 PDF·그림을 특허증으로 읽는다. 회사자료 특허목록 시트가 비어 있으면 파일 수를 특허 건수로
자기평점 '전문기술(특허)'에 쓰고(파일 하나 = 1건), 보유기술을 평가하는 공고면 특허증 원본을 통합본.pdf 끝에 붙인다.
