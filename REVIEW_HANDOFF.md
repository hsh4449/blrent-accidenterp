# REVIEW_HANDOFF — BL 사고대차 관리 ERP (blrent-accidenterp)

작성일 2026-09-09 · 기준 커밋 `main` 5ea9807 (2026-09-07) · 작성 방식: 실제 코드·라이브 DB·배포 상태를 읽고 정리. 코드는 수정하지 않음.

표기 규칙
- **[사실]** 코드·DB·배포 콘솔에서 직접 확인함 (근거 경로 병기)
- **[추정]** 코드를 읽고 내린 해석. 실행으로 확인하지 않음
- **[미확인]** 확인하지 못함. 검수자가 직접 확인 필요

---

## 1. 프로젝트 개요

### 1.1 업무 [사실]
렌터카 회사의 **사고대차(보험 대차) 청구·입금 관리** 화면. 외부 시스템 IMS Form(imsform.com)에 있는 사고대차 계약을 크롤링해 자체 DB에 쌓고, 화면에서 청구·입금 현황, 차량별 가동률, 월 정산을 보며, **미입금 건을 보험사 담당자에게 독촉 문자(LMS)로 자동·수동 발송**한다.

구성 요소 3개 (`send_module/README.md`, `send_module/worker.py:1-9`)
1. 프론트 SPA `index.html` (GitHub Pages)
2. 크롤러 `crawler.py` (IMS → `accident_rentals` upsert)
3. 독촉 SMS 백엔드 `send_module/` (Railway 워커 1개 프로세스에서 크롤러까지 스케줄 실행)

### 1.2 사용자 역할과 접근 권한 [사실]
- 로그인 개념은 **4자리 사원코드** 하나. `index.html:250-262` `USER_VIEW_CODES` 상수에 코드 → owner 매핑이 **소스에 평문**으로 들어 있다 (값은 이 문서에 적지 않음).
- owner 11개: `hq`(본사) + 담당자 10명(`jiip, kim, park, good, jang, choi, jeon, yu, kang, han`). 담당자 실명은 `index.html:271` `OWNER_LABEL` 참조.
- 권한 차이는 **클라이언트 필터**뿐이다 (`index.html:575-582` `matchOwner`).
  - `hq`: 상단 담당 탭으로 모든 owner 데이터 열람·수정 가능 (`HQ_OWNER_TABS`, `index.html:270`). '전체' 탭에서는 독촉 발송만 비활성 (`index.html:642`).
  - 담당자 owner: 자기 owner 행만 표시. `kim`, `jiip` 는 차량 끝4자리 기준 하위 탭이 더 있음 (`index.html:278-309`).
- 코드 입력 결과는 `localStorage('blrent_user_owner')` 에 저장되어 새로고침 후 유지 (`index.html:521-528`). 서버 세션·만료 없음.
- **[사실]** DB 쪽에는 역할 검증이 전혀 없다. RLS 꺼짐 + anon 키 하나로 모든 테이블 read/write (5장 참조).

### 1.3 핵심 업무 흐름 [사실]
1. **수집**: Railway 워커가 KST 09~17시 매시 17분 `crawler.py` 실행 → IMS 로그인 → 차량 끝4자리 목록으로 검색 → `accident_rentals` upsert (`crawler.py:603-769`, `send_module/worker.py:56`).
2. **열람·수정**: 화면 8탭(대시보드/입금완료/계약내역/정산/스케줄/차량/독촉발송/휴지통, `index.html:939`). 계약 수정·신규·엑셀 가져오기/내보내기, 차량 상태 변경, 월 고정비 입력.
3. **독촉 자동발송**: 매일 08:30 KST `auto_send.py` → owner 별 `auto_send_enabled` 확인 → 청구완료·미입금·청구일+1일 경과 건을 보험사 담당자 전화번호로 묶어 솔라피 LMS 발송 → `accident_sms_logs` 기록 (`send_module/auto_send.py:36-76`, `send_engine.py:53-80, 192-281`).
4. **독촉 수동발송**: 화면 [문자발송]/[전체발송] → `accident_manual_send_queue` INSERT → 워커가 2분마다 큐 처리 (`index.html:2311-2362`, `process_manual_queue.py`).
5. **입금 처리**: 화면에서 입금완료 처리하거나 크롤러가 IMS 입금 정보를 덮어씀. 보류/분심 등은 `accident_excluded_contracts` 마커로 발송 제외 (`index.html:2365-2410`).

---

## 2. 기술 구성과 실행 방법

### 2.1 스택 [사실]
| 영역 | 내용 | 근거 |
|---|---|---|
| 프론트 | 단일 `index.html` 2,753줄. React 18.2.0 **development 빌드** + Babel Standalone 7.23.5 로 브라우저에서 JSX 변환. SheetJS 0.20.0, supabase-js UMD (`@2` 로 minor 미고정). 빌드 도구·npm 없음 | `index.html:12-22` |
| 백엔드 | Python 3.12. `supabase>=2`, `requests>=2.31`, `apscheduler 3.x`. 워커는 스크립트를 서브프로세스로 실행 | `requirements.txt`, `send_module/worker.py:25-42` |
| DB | Supabase Postgres, 프로젝트 ref `jjwsnwnfhqcszwmjdcac`. **BL매니저(blrent-car-system)와 같은 프로젝트를 공유** | `index.html:56`, `.env.example` |
| 외부 서비스 | IMS Form(크롤 대상, `api.rencar.co.kr` 인증), 솔라피 SMS API, GitHub Pages, Railway | `crawler.py:181`, `solapi_sender.py:39` |

### 2.2 로컬 실행·테스트 명령 [사실 + 주의]
```bash
# Python 의존성
pip install -r requirements.txt            # supabase, requests, apscheduler
# 문법 검사만 (부작용 없음) — 2026-09-09 통과 확인
python -m py_compile crawler.py send_module/*.py
# 프론트: 정적 파일이라 그냥 브라우저로 index.html 열면 됨 (CDN 접속 필요). 로컬 서버 예:
python -m http.server 8000
```
**실행하면 실제 부작용이 나는 스크립트** (테스트 목적으로 돌리지 말 것)
- `python send_module/auto_send.py` → 실제 독촉 LMS 발송 (`MASTER_KILL_SWITCH=False`, `send_engine.py:43`)
- `python send_module/process_manual_queue.py` → 큐에 남은 요청 실발송
- `python crawler.py` → IMS 로그인 + 라이브 `accident_rentals` upsert
- `python send_module/worker.py --once` → 위 3개 전부 1회 실행 (`worker.py:48-51`)
- 자동화된 테스트는 **없음** (6장).

### 2.3 환경변수 (이름과 용도만) [사실]
근거: `send_module/.env.example`, `send_module/db.py:11-17`(`.env` 자동 로드), `crawler.py:16-19`, Railway 변수 목록(2026-09-09 조회).

| 이름 | 용도 | 사용처 |
|---|---|---|
| `SUPABASE_URL` | Supabase 프로젝트 URL | db.py, crawler.py |
| `SUPABASE_KEY` | **service_role 키** (백엔드). 프론트는 별도로 anon 키를 소스에 하드코딩 | db.py, crawler.py |
| `IMS_ID`, `IMS_PW` | IMS Form 로그인 (PW 는 SHA-256 해시 후 전송) | crawler.py:173-198 |
| `SOLAPI_API_KEY`, `SOLAPI_API_SECRET` | 솔라피 HMAC 인증 | solapi_sender.py:14-15 |
| `SOLAPI_FROM_HQ`, `SOLAPI_FROM_JIIP`, `SOLAPI_FROM_KIM`, `SOLAPI_FROM`(legacy) | owner 별 발신번호. jiip 외 owner 는 HQ 번호로 fallback | solapi_sender.py:22-33 |
| `TZ` | 워커 스케줄 기준 (`Asia/Seoul`) | worker.py:9 |
| `DRY_RUN`, `NOTIFY_OWNER`, `OWNER_NAME`, `SOLAPI_REPORT_FROM`, `REPORT_LMS_MAX_BYTES` | **[미확인]** Railway 에 앞 3개가 설정돼 있으나 `main` 코드에는 참조가 없음. 뒤 2개는 미머지 브랜치 `feature/result-report` 의 `result_report.py:33-35` 가 읽음 (4.5 참조) | — |

- GitHub Actions 시크릿 이름: `IMS_ID`, `IMS_PW`, `SUPABASE_URL`, `SUPABASE_KEY` (백업용 수동 실행 워크플로, `.github/workflows/daily-crawl.yml`).
- 실제 값 보관처는 저장소 밖(`C:\Projects\_vault`, 이 문서에 포함하지 않음).

### 2.4 버전 [사실]
- Python 3.12.7 (로컬), GitHub Actions 는 3.12 지정. 패키지 매니저 pip. lock 파일 없음.
- 로컬 설치 확인(2026-09-09): supabase 2.27.2, requests 2.32.5, bs4 4.14.3. **apscheduler 는 로컬 미설치** (워커 로컬 실행 불가 상태).
- Node/npm 없음. 프론트 라이브러리는 전부 CDN.

---

## 3. 코드 구조

### 3.1 폴더 [사실]
```
index.html                      프론트 전체 (React SPA, 2,753줄)
crawler.py                      IMS 크롤러 + 차량·owner 매핑 상수 (773줄)
requirements.txt                Railway 빌드용 의존성 (send_module/requirements.txt 와 수동 동기화)
send_module/
  worker.py                     Railway 진입점. APScheduler 로 3개 스크립트 실행
  auto_send.py                  08:30 자동 독촉 진입점 (owner 별 게이트)
  process_manual_queue.py       2분 주기 수동 발송 큐 처리
  send_engine.py                미입금 조회 → 그룹핑 → 본문 → 솔라피 발송 → 로그
  solapi_sender.py              HMAC 헤더, 발신번호 선택, byte 계산
  db.py                         Supabase 클라이언트, .env 로더, KST 헬퍼
  run.sh / README.md / .env.example   (Vultr cron 시절 문서, 일부 옛 내용)
supabase/migrations/            SQL 6개 (2026-05-19 ~ 08-03). 전체 스키마를 담고 있지 않음 (5.1)
.github/workflows/daily-crawl.yml  수동 실행 전용 백업 크롤 워크플로
```

### 3.2 기능별 파일 위치 [사실]
| 기능 | 위치 |
|---|---|
| 코드 입력(락 화면) / 로그아웃 | `index.html:872-909`, `:930`, `:961` |
| owner 필터·담당 탭 | `index.html:250-309`(상수), `:573-583`(matchOwner), `:975-1046`(탭 UI) |
| 데이터 로드 | `index.html:644-677` (4개 테이블 병렬 select, 실패 시 `INITIAL_DATA` 폴백 `:671`) |
| 계약 저장·수정 | `doSave` `:793-803`, `EditForm` `:2110-2163`, DB 매핑 `toDbRow/fromDbRow` `:61-116` |
| 엑셀 가져오기/내보내기 | `parseIMSExcel` `:315-356`, `onImport` `:778-790`, `exportToExcel` `:367-386` |
| 삭제(휴지통)/복구/영구삭제/비우기 | `:805-867` |
| 차량 관리(fleet) | `saveFleet/addFleet/deleteFleet` `:687-720`, `FleetTab` `:1996-2069` |
| 월 고정비 | `saveMonthlyFixed` `:722-736`, `SettlementTab` `:1719-1927` |
| 정산 계산(부가세·수수료 15%) | `:1750-1757` |
| 보류/입금 상태 변경 | `onChangeExclusionStatus` `:738-769`, `DokchokTab.onChangeStatus` `:2365-2410` |
| 독촉 탭(설정·미리보기·수동발송·이력) | `DokchokTab` `:2175-2745` |
| 크롤러 차량 목록·owner 이관 이력 | `crawler.py:22-146` (`VEHICLE_NUMBERS`…`OWNER_TRANSFERS`) |
| 크롤러 row 변환·상태 매핑 | `crawler.py:161-170`, `:264-438` |
| 크롤러 upsert·보정 로직 | `crawler.py:630-767` |
| 자동발송 게이트 | `auto_send.py:36-76` |
| 발송 엔진·킬스위치 | `send_engine.py:43`, `:192-281` |

### 3.3 로그인·권한·데이터 접근이 처리되는 곳 [사실]
- **로그인**: `index.html:873-880` `tryEnter` 가 입력 4자리를 `USER_VIEW_CODES` 키와 비교. 서버 호출 없음.
- **권한 검사**: 서버 측 없음. 프론트 `matchOwner`(`:573`)와 `dokSendDisabled`(`:642`)가 전부.
- **데이터 조회·수정**: 프론트가 supabase-js 로 테이블에 직접 select/upsert/update/delete (`index.html` 내 `.from('accident_…')` 호출 19곳). 백엔드는 service_role 키로 같은 테이블 접근.

---

## 4. 기능별 현재 상태

### 4.1 구현 상태 표
| 기능 | 상태 | 근거·비고 |
|---|---|---|
| 코드 락 화면, owner 필터, 11 owner 탭 | 구현 완료 [사실] | 락 화면 렌더링 확인(6.2) |
| 대시보드·입금완료·계약내역·정산·스케줄·차량·휴지통 탭 | 구현 완료 [사실, 코드 기준] | 화면 조작은 미수행 (6.3) |
| 엑셀 IMS 형식 가져오기 | 구현됨 [사실] / 데이터 덮어쓰기 위험 [추정] | 4.3 #2 |
| 크롤러(IMS → DB, 교체건 분리, 분할입금 수집) | 구현 완료·운영 중 [사실] | Railway 로그 미열람 |
| 자동 독촉 LMS | 운영 중 [사실] | 2026-09-09 08:30 KST `jiip`, `jang` 발송 로그 HTTP 200 확인. `hq` 는 2026-09-08 에 `auto_send_enabled=false` 로 꺼짐(`updated_by` = `ui:erp:claude:2026-09-08`). **이유 [미확인]** |
| 수동 발송 큐 | 구현 완료 [사실] | 큐 마지막 사용 2026-06-30 (7건) |
| 자동발송 결과 보고 문자 | **미머지 브랜치에서 운영 중** [사실] | 4.5 |
| 테스트 코드 | 미구현 [사실] | |
| 킬스위치 `send_armed` 1회 무장 | 폐기(코드 미참조, 컬럼만 잔존) [사실] | `README.md:23`, DB 컬럼 존재 |

### 4.2 실제 동작을 확인한 범위 (2026-09-09)
- [사실] GitHub Pages `https://hsh4449.github.io/blrent-accidenterp/` HTTP 200, Playwright 로 열어 락 화면(제목·코드 입력창) 렌더링 확인. 콘솔: 에러 1(favicon 404), 경고 1(Babel in-browser). **코드 입력 이후 화면은 열지 않음** (코드는 자격증명이고 라이브 DB에 쓰기 가능).
- [사실] 라이브 DB 스키마·RLS·제약·인덱스·행 수 조회(읽기 전용). 5.1 에 반영.
- [사실] Railway `accident-worker` 최신 배포 SUCCESS (2026-09-09 00:21 KST), 시작 명령 `python send_module/worker.py`.
- [사실] 자동발송 로그: 2026-09-09 08:30 KST `jiip`·`jang` LMS, 솔라피 200.
- [사실] Python 7개 파일 `py_compile` 통과.
- 실행하지 않음: 크롤러, 자동발송, 큐 처리, 워커, 화면 CRUD (모두 라이브 데이터·실문자 부작용).

### 4.3 알려진 문제·재현 방법
번호 순서는 심각도 순이 아님. 7.3 에 우선순위 정리.

1. **[사실] 운영 워커가 `main` 이 아닌 `feature/result-report` 를 돌리고 있고, 그 브랜치는 2026-09-07 `main` 변경을 포함하지 않는다.**
   - 브랜치 base = d27e09f (35c8cd5·5ea9807 이전). Railway 배포 sha b1dfd33.
   - `git diff origin/main..origin/feature/result-report -- crawler.py` 결과: `1477`(hq→jiip 이관), `8681`·`9588`(kim 신규), `9587`(jeon 신규·`JEON_VEHICLES`), `9194` 의 kim 이관 hop 이 **없고**, 제외했던 `5438` 이 남아 있음.
   - 재현: 워커 로그에서 `[SEARCH] 1477` 유무 확인, 또는 `accident_rentals` 에서 9194 의 9/1 이후 시작 건 owner 가 크롤 뒤 `jang` 으로 되돌아가는지 확인.
2. **[추정] 엑셀 가져오기가 기존 행의 수기 입력값을 지우고 owner 를 `hq` 로 되돌릴 수 있다.**
   - `mergeContracts`(`index.html:358-364`)는 같은 id 면 incoming 객체로 **통째 교체**. `parseIMSExcel` 결과에는 owner, 담당자 연락처, 탁송료, 수수료 필드가 없음 → `toDbRow`(`:59`)가 `owner:'hq'`, `insurance_manager_phone:null`, 수수료 0 으로 upsert.
   - 재현: 담당자 연락처가 입력된 계약을 포함한 IMS 엑셀을 가져오기 → 해당 계약의 담당자 연락처·owner 확인. (미실행)
3. **[추정] 화면에서 수기로 '입금완료' 처리한 건을 크롤러가 되돌릴 수 있다.**
   - `crawler.py:636-644`: 입금완료라도 `rental_fee=0` 이면 재크롤 대상. `convert_claim` 은 IMS 상태·`claim_done_at` 으로 `status`, `deposit_date` 를 다시 계산해 upsert (`:333-335`, `:405`) → IMS 에 입금 기록이 없으면 `청구완료`/`deposit_date NULL` 로 덮어써 독촉 대상으로 복귀.
   - 주석(`:632`)은 이 재크롤을 의도한 것으로 적고 있으나 상태 되돌림까지 의도인지는 [미확인].
4. **[사실] 수동 발송 중복 방지가 클라이언트에만 있다.** 1시간 cooldown 은 큐 테이블을 읽어 계산하지만(`index.html:2208-2215`) 큐 INSERT 자체에는 서버 측 중복 검사가 없고, [전체발송] 1분 잠금은 `setTimeout` 로컬 상태(`:2337`). `process_manual_queue.py` 에도 cooldown 게이트 없음(`:13-16`). 두 PC 에서 동시에 누르면 같은 담당자에게 2통.
5. **[사실] Supabase 로드 실패 시 2026-04 시점 내장 데이터 77건으로 조용히 폴백** (`index.html:671`, `console.error` 만). 사용자는 오래된 데이터를 최신으로 오인할 수 있음. 이 내장 데이터는 4.4 #1 의 개인정보 문제이기도 함.
6. **[사실] 문서·주석과 코드 불일치**: `send_module/README.md:12-14` 는 "발송 차단 상태(`MASTER_KILL_SWITCH=True`)" 라 하나 코드는 `False`(`send_engine.py:43`). `index.html:2166-2173`, `run.sh:6-8` 의 3중 잠금·send_armed 설명도 옛 내용. 화면 문구에 "Vultr 큐 처리기"(`:2324`, `:2351`), "본사·신동석·김민규 중 하나"(`:2312`, `:2344`, `:2470`) 등 owner 3개 시절 문구 잔존.
7. **[사실] `accident_excluded_contracts` 삭제가 계약내역 탭에서는 owner 필터 없이 실행** (`index.html:750`, `:753`) — 독촉 탭은 owner 조건 포함(`:2401`). PK 가 `contract_id` 라 실질 영향은 없을 가능성이 높음 [추정].
8. **[사실] 크롤러 로그에 고객명이 출력됨** (`crawler.py:642`, `:673`) → Railway 로그에 개인정보 잔존.
9. **[사실] 프로덕션에서 React development 빌드 + 브라우저 Babel 사용** (콘솔 경고 확인). 초기 로드 성능·소스 노출.
10. **[사실] 저장소 루트에 `.gitignore` 없음** → `__pycache__/` 가 untracked 로 보임. `send_module/.gitignore` 만 존재.

### 4.4 임시 처리·하드코딩·TODO
1. [사실] `index.html:135-232` `INITIAL_DATA`: 실고객 계약 77건(고객명·연락처·차량번호 포함)이 소스에 내장. 저장소 공개 상태(5.4).
2. [사실] Supabase URL·anon 키 하드코딩 `index.html:56-57` (RLS 꺼짐이라 사실상 전권 키).
3. [사실] 사원코드·owner·발신번호 표시값 하드코딩 `index.html:250-262`. 발신번호 표시값은 UI 라벨일 뿐, 실제 발신번호는 Railway 환경변수(`solapi_sender.py:22-33`) — 두 값의 일치 여부 [미확인].
4. [사실] 차량 목록·이관 이력 하드코딩 `crawler.py:22-134`. 차량 추가·이관 시 `crawler.py` + `accident_fleet` + (해당 시) `index.html:278-309` 세 곳을 손으로 맞춰야 함.
5. [사실] `KIM_MANAGER_VEHICLES`·`JIIP_MANAGER_VEHICLES` (`index.html:278-309`)는 과거 이력 필터용으로 유지(코멘트). `KIM_MANAGER_VEHICLES` 의 담당자 표기 중 하나는 다른 저장소에서 오기로 확인된 철자를 그대로 씀 [사실, 이름은 생략].
6. [사실] `MIN_OVERDUE_DAYS=1` 이 프론트(`index.html:2230`)와 백엔드(`send_engine.py:26`)에 각각 상수로 존재 — 수동 동기화.
7. [사실] `requirements.txt` 와 `send_module/requirements.txt` 이중 관리(주석 `requirements.txt:1`).
8. [사실] 코드 내 `TODO/FIXME` 검색 결과 0건. 의도적 하드코딩은 소유자 통제 방식이라는 설명이 `supabase/migrations/20260519_accident_dokchok.sql:92-94` 에 있음.

### 4.5 미머지 브랜치 `feature/result-report` [사실]
- 커밋 b1dfd33 (2026-09-09 00:18 KST), 이 로컬 clone 에는 fetch 전까지 없었음. 변경: `send_module/result_report.py` 신규 296줄, `auto_send.py`·`send_engine.py` 소폭, `crawler.py` 는 **main 보다 뒤처진 상태**.
- 내용: 자동발송 뒤 owner 담당자에게 "솔라피 접수 결과" 문자를 보내고 `accident_result_reports` 에 기록. DB 에 해당 테이블과 `accident_send_settings.report_enabled/report_phone` 컬럼이 이미 존재하고(`jiip`, `jang` 만 enabled) 2026-09-09 자 `registered` 2행 확인.
- 이 테이블·컬럼의 DDL 은 어느 브랜치에도 없음.

---

## 5. 데이터와 보안

### 5.1 스키마 (2026-09-09 라이브 DB 조회 결과) [사실]
관계는 전부 **논리적**이다. FK 제약 없음. 조인 키는 `accident_rentals.id`(IMS claim id 문자열)와 `owner`, `vehicle_number`.

| 테이블 | PK | 핵심 컬럼 | 행 수 | 저장소 내 DDL |
|---|---|---|---|---|
| `accident_rentals` | `id` text | owner, status, 날짜·시간 8종, 고객 4종, 보험사·담당자 2종, 금액(billing_amount, rental_fee, delivery_fee_1/2, commission_*), replacement_* 6종, `deposits` jsonb, is_deleted/deleted_at | 1,707 (입금완료 1,393 · 청구완료 162 · 청구전 67 · 교체 50 · 배차중 35, 삭제 0) | **없음** (owner 컬럼 추가만 `20260520_owner_column.sql`) |
| `accident_fleet` | `vehicle_number` | model, status, owner, note, status_changed_at | 97 (active 91, sold 4, paused 1, maintenance 1) | **없음** (owner 추가·시드만) |
| `accident_send_settings` | `owner` | auto_send_enabled, send_armed(미사용), cutoff_billing_date, message_template, last_auto_send_date, **report_enabled, report_phone**(DDL 없음), id CHECK(id=1) 잔존 | 11 | `20260519`, `20260520_send_settings_owner_split` |
| `accident_sms_logs` | `id` | owner, trigger_type, recipient_phone, contract_ids[], message_text, solapi_* | 2,424 | `20260519` |
| `accident_excluded_contracts` | `contract_id` | owner, reason, excluded_by | 16 | `20260519` |
| `accident_manual_send_queue` | `id` | owner, target_phones[], requested_by, processed_at, result jsonb | 7 | **없음** (CHECK 재정의만 `20260803`) |
| `accident_result_reports` | `id` | owner, run_date, part_no/part_total, kind, status, recipient_phone, message_text, solapi_* | 2 | **없음** |
| `vehicle_monthly_fixed` | `(vehicle_number, year_month)` | installment, insurance, etc_cost, etc_note | 54 | **없음** |

- owner CHECK 제약: 6개 `accident_*` 테이블 모두 11개 owner 로 재정의되어 있음. 저장소 마이그레이션은 5개 owner 까지만(`20260803_owner_park_good.sql:21-37`). 11개 확장 SQL 은 저장소에 없음.
- 인덱스: owner, `is_deleted=true` 부분 인덱스, 큐 미처리 부분 인덱스 등 존재.
- 결론: **`supabase/migrations/` 만으로는 DB 를 재구성할 수 없다.** 검수 시 라이브 DB 를 정본으로 볼 것.

### 5.2 사용자·조직별 데이터 접근 제한 [사실]
- 없음. 프론트 필터(`matchOwner`)가 유일. `localStorage` 의 `blrent_user_owner` 값을 `hq` 로 바꾸면 본사 화면이 열린다 (`index.html:524-525` 는 값이 `ALL_OWNERS` 에 있으면 그대로 신뢰).
- RLS: `accident_*` 7개 테이블 모두 `relrowsecurity=false`. `vehicle_monthly_fixed` 만 RLS on 이나 정책 `vehicle_monthly_fixed_all` 이 `USING true / WITH CHECK true / roles {public}` → 사실상 개방.
- 이 상태는 소유자가 **의도한 정책**이다: 외부인 미사용·사용자 3~4명 전제 (`20260519_accident_dokchok.sql:91-94`). 검수자는 "정책 자체를 바꿀지"가 아니라 "그 전제가 아직 성립하는지"(5.4)를 판단하면 된다.

### 5.3 관리자 권한 검증 [사실]
- 없음. 4자리 코드 비교가 전부이고 코드는 페이지 소스에 있다.
- 백엔드 스크립트는 service_role 키로 동작하며 호출자 검증 개념이 없다(스케줄 실행만 가정).

### 5.4 개인정보·파일 업로드·API 키 [사실]
- **GitHub 저장소가 PUBLIC** 이고 Pages 도 공개. 따라서 다음이 인터넷에 공개돼 있다: (a) anon 키 + RLS off → 누구나 모든 테이블 read/write, (b) `INITIAL_DATA` 고객 77건(이름·연락처·차량번호), (c) 사원코드 전체, (d) `accident_manual_send_queue` INSERT 가 열려 있어 **누구나 보험사 담당자에게 실문자 발송을 트리거할 수 있음**. → 7.3 #1.
- 개인정보 필드: `accident_rentals.customer_name/customer_phone/customer_number`, `insurance_manager_name/phone`, `accident_sms_logs.recipient_phone/message_text`, `accident_send_settings.report_phone`. 암호화·마스킹 없음. 엑셀 내보내기(`index.html:361`)에 고객 연락처 포함.
- 파일 업로드: 서버 저장 없음. 브라우저에서 `.xlsx/.xls` 를 SheetJS 로 파싱해 DB upsert (`index.html:916`, `:778`). 크기·행 수 제한 없음.
- API 키: 백엔드 키는 환경변수(Railway/Actions 시크릿). 프론트 anon 키는 소스. IMS 비밀번호는 SHA-256 해시로 전송(`crawler.py:175`).

### 5.5 중요 작업 보호 장치 [사실]
| 작업 | 보호 | 위치 |
|---|---|---|
| 계약 삭제 | `confirm` 1회 → soft delete(`is_deleted`) | `index.html:806` |
| 영구 삭제 / 휴지통 비우기 | `confirm` 1회 → hard DELETE. 감사 로그 없음. 크롤이 다시 가져올 수 있음(문구에 명시) | `:843`, `:857` |
| fleet 삭제 | `confirm` 1회 → DELETE | `:714` |
| 엑셀 가져오기 | 확인 없음. 전체 merged 배열 upsert | `:786` |
| 자동 독촉 | 코드 상수 `MASTER_KILL_SWITCH`(현재 False) + owner 별 `auto_send_enabled` + 일요일 제외 + 하루 1회 + 청구+1일 + cutoff + 보류 마커 | `send_engine.py:43`, `auto_send.py:24-63` |
| 수동 독촉 | `confirm` + 클라이언트 cooldown. 서버 측 중복 방지 없음(4.3 #4). 큐 1회 최대 20건 | `process_manual_queue.py:30` |
| 발신번호 누락 | owner 발신번호가 비면 발송 중단 | `send_engine.py:226-233` |
| 크롤 upsert | `rental_fee` 이상치 보호, 배차중 중복 보정, 담당자 연락처는 IMS 값이 비면 기존값 보존 | `crawler.py:433-436`, `:665-677` |

---

## 6. 검증 현황

### 6.1 기존 테스트 [사실]
- 없음. `test*`, `pytest.ini`, `pyproject.toml`, `package.json` 모두 없음. CI 도 없음(Actions 는 크롤 수동 실행 워크플로뿐).
- 과거 UI 리팩토링 검증은 "라이브 DB 로 A/B 텍스트 해시 비교(Playwright)" 를 수동으로 했다는 기록이 있으나 스크립트는 저장소에 없음 [미확인].

### 6.2 최근 검증 결과 (전부 2026-09-09, 이 문서 작성 시)
| 항목 | 결과 |
|---|---|
| `py_compile` 7개 파일 | 통과 |
| GitHub Pages 로드 + 락 화면 렌더링 | 정상. JS 에러 0(favicon 404 제외) |
| Pages 빌드 | 최근 5회 success (마지막 2026-09-07 13:21 KST) |
| Railway `accident-worker` | 최신 배포 SUCCESS, 단 브랜치 `feature/result-report` (4.3 #1) |
| 자동발송 실행 | 2026-09-09 08:30 KST `jiip`·`jang` 발송, 솔라피 200 |
| DB 스키마·제약·RLS | 조회 완료, 5.1 반영 |
| GitHub Actions 크롤 워크플로 | 마지막 실행 2026-06-19 (수동). schedule 은 비활성 |

### 6.3 실행하지 못한 검증과 이유
- 크롤러·자동발송·큐 처리 실행: 실문자 발송·라이브 DB 변경 부작용.
- 코드 입력 후 화면 CRUD: 라이브 DB 에 쓰기 발생, 테스트 DB 없음.
- Railway 워커 로그 열람: 이번 조사 범위에서 생략. `feature/result-report` 실행 로그 확인 필요.
- 발신번호 실제값과 UI 표시값 대조: 환경변수 값 미열람.
- 엑셀 가져오기 덮어쓰기(4.3 #2), 입금완료 되돌림(4.3 #3): 재현하려면 라이브 데이터 변경 필요.

### 6.4 테스트가 없는 핵심 기능
크롤러 `convert_claim`·`build_extra_rows`(교체건 분리, owner 이관 hop), 정산 계산(`index.html:1750-1757`), 독촉 대상 선정(`send_engine.load_unpaid_contracts`), 본문 템플릿 치환, 엑셀 파서, 분할입금 월 귀속(`depositItems`, `index.html:118-131`).

---

## 7. 설계 배경과 검수 우선순위

### 7.1 중요한 설계 결정과 이유
- **빌드 없는 단일 HTML + CDN** [사실]: 배포는 `main` push → Pages. 툴체인 없이 소유자 혼자 유지보수하기 위한 선택 [추정].
- **RLS off·anon 전권** [사실]: 소유자 명시 정책(5.2). 전제는 "외부인 미사용·소수 사용자".
- **owner 컬럼으로 본사/담당자 분리** [사실]: `20260520_owner_column.sql` 배경 주석. 별도 테이블 대신 한 테이블 + 필터. 이관은 계약 시작일 기준 hop 으로 과거 매출 이력을 이전 owner 에 보존(`crawler.py:90-93`).
- **크롤이 정본, 화면 수정은 보조** [사실]: 크롤러가 매시간 upsert 하므로 화면 수기값은 크롤러가 보호하는 필드(담당자 연락처, rental_fee 이상치)만 살아남는다(`crawler.py:433`, `:665`). 그 외 필드는 IMS 값이 이긴다.
- **독촉 자동발송 안전장치 단순화** [사실]: 2026-05-17 오발송 사고 후 3중 잠금 도입 → 05-20 에 `send_armed` 폐기, ON/OFF 단일 토글(`README.md:23-27`). 코드 킬스위치는 사고 시 수동으로 `True` 로 올리는 용도.
- **수동 발송을 큐로 우회** [사실]: 프론트에 솔라피 키를 두지 않기 위해 DB 큐 → 워커 발송 구조(`index.html:2341-2342`).
- **Vultr cron → Railway 워커 1프로세스** [사실]: 2026-09-06 이전. 기동 시 크롤은 돌리지 않음(IMS 반복 로그인 방지, `worker.py:47`).

### 7.2 변경 시 파급이 큰 부분
- `crawler.py:22-146` 차량·이관 상수: 잘못 바꾸면 매시간 크롤이 owner 를 전 행에 다시 써서 담당자별 매출 이력이 바뀐다. 백필 순서도 중요(크롤러 배포 → DB 백필).
- `index.html:61-116` `toDbRow/fromDbRow`: 컬럼 누락 시 upsert 로 NULL 덮어쓰기.
- `send_engine.load_unpaid_contracts` 조건(`:53-80`)과 프론트 `unpaidRaw`(`index.html:2231-2242`): 두 곳이 같은 규칙을 따로 구현. 한쪽만 바꾸면 화면과 실제 발송 대상이 어긋난다.
- `accident_send_settings.message_template`: DB 값이 본문. 변수명 변경 시 `send_engine.py:115-121` 과 프론트 미리보기(`index.html:2271-2293`) 동시 수정.
- owner 추가: CHECK 6개 재정의 + settings 시드 + `USER_VIEW_CODES` + `HQ_OWNER_TABS`/`OWNER_LABEL` + `crawler.py` 목록 + `auto_send.OWNERS`(자동발송 원할 때).
- `requirements.txt` 이중 관리: 한쪽만 바꾸면 Railway 빌드 실패 이력 있음(커밋 6329bae).

### 7.3 검수자가 먼저 확인할 위험 10개
| # | 위험 | 근거 |
|---|---|---|
| 1 | **공개 저장소·공개 페이지에 anon 키(RLS off)·사원코드·고객 77건이 노출.** 누구나 DB 전체 읽기·쓰기·삭제, 큐 INSERT 로 실문자 발송 가능. "외부인 미사용" 전제가 깨져 있음 | `index.html:56-57`, `:135-232`, `:250-262`; GitHub visibility PUBLIC(2026-09-09 조회); 5.2 RLS 조회 |
| 2 | **운영 워커가 `main` 보다 뒤처진 `feature/result-report` 를 실행 중.** 09-07 차량·이관 변경이 크롤에서 빠져 owner 가 되돌아갈 수 있음. 브랜치 병합 또는 Railway 브랜치 설정 확인 필요 | 4.3 #1; Railway 배포 메타 |
| 3 | 스키마 정본이 저장소에 없음. 4개 테이블·11 owner CHECK·report 컬럼 DDL 부재 → 재구성·리뷰 불가 | 5.1 |
| 4 | 화면 권한이 클라이언트 필터뿐. `localStorage` 조작으로 본사 권한 획득 | `index.html:521-528`, `:575-582` |
| 5 | 엑셀 가져오기가 기존 행을 통째 교체해 수기값·owner 를 지울 수 있음 | `index.html:358-364`, `:786`; 4.3 #2 |
| 6 | 수기 입금완료를 크롤러가 되돌릴 수 있음(독촉 재개 → 오발송 경로) | `crawler.py:636-644`, `:333-335`; 4.3 #3 |
| 7 | 수동 발송 중복 방지가 서버에 없음. 동시 클릭 시 담당자에게 중복 LMS | `index.html:2337`, `process_manual_queue.py:13-16`; 4.3 #4 |
| 8 | 영구삭제·휴지통 비우기·fleet 삭제가 `confirm` 하나로 hard DELETE, 감사 로그 없음 | `index.html:843-867`, `:714` |
| 9 | 문서와 코드 불일치(킬스위치 상태, 3중 잠금, Vultr 문구). 운영자가 "차단 상태"로 오인할 수 있음 | `README.md:12-14` vs `send_engine.py:43`; 4.3 #6 |
| 10 | Supabase 실패 시 4월 데이터로 조용히 폴백 + 프로덕션에서 dev React/Babel + CDN minor 미고정 | `index.html:671`, `:12-22`; 4.3 #5, #9 |

추가로 확인 권장 (10개 밖): `hq` 자동발송이 2026-09-08 에 꺼진 경위, Railway 의 `DRY_RUN`/`NOTIFY_OWNER`/`OWNER_NAME` 변수 용도, 크롤 로그 개인정보(4.3 #8), IMS 비공식 엔드포인트 의존(`crawler.py:181`, `:563`) 변경 리스크.

---

## 8. 검수자에게 전달할 파일 / 제외할 파일

### 전달 (저장소 트래킹 파일 전부, 17개)
```
REVIEW_HANDOFF.md                      이 문서
index.html
crawler.py
requirements.txt
send_module/worker.py
send_module/auto_send.py
send_module/process_manual_queue.py
send_module/send_engine.py
send_module/solapi_sender.py
send_module/db.py
send_module/README.md                  ※ 일부 옛 내용 (4.3 #6)
send_module/.env.example               ※ 값 없음, 키 이름만
send_module/run.sh                     ※ Vultr 시절 스크립트, 현재 미사용
send_module/.gitignore
send_module/requirements.txt
supabase/migrations/*.sql (6개)        ※ 전체 스키마 아님 (5.1)
.github/workflows/daily-crawl.yml
```
- 함께 전달하면 좋은 것: `origin/feature/result-report` 브랜치(특히 `send_module/result_report.py`), 라이브 DB 스키마 덤프(5.1 표를 대체할 `information_schema` 출력).
- **전달 전 조치 권장**: `index.html:135-232` `INITIAL_DATA` 는 실고객 정보이므로 검수용 사본에서는 빈 배열로 치환하거나 마스킹. (저장소 자체가 공개라 이미 노출돼 있지만, 검수 자료로 재배포하지 않기 위함.)

### 제외
```
__pycache__/, *.pyc                    로컬 산출물 (untracked)
send_module/.env                       실제 키 (존재 시)
C:\Projects\_vault\**                  자격증명 정본
Railway/GitHub 시크릿 값               이름만 2.3 에 기재
accident_rentals·sms_logs·send_settings 등 DB 데이터 덤프   고객·담당자 개인정보 포함
.playwright-mcp/                       이번 검증 스냅샷·콘솔 로그(로컬 임시)
```
