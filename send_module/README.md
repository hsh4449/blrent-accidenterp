# send_module — 사고대차 미입금 자동 독촉 SMS

매일 KST **08:30** Vultr cron 진입 → `contracts.status='청구완료' AND deposit_date IS NULL` 인 건들을
보험사 담당자별로 묶어 솔라피 LMS 발송. **매일 1회 (일요일 제외)** — 본사(hq)/지입(jiip) 둘 다 동일.

ERP 컨트롤 UI: `/ (사고대차관리)` → 우상단 **"독촉발송"** 탭.

> ⚠ **현재 발송 차단 상태입니다.**
> 사용자(황성현)의 명시적 `"사고대차 킬스위치 해제"` 지시가 있기 전까지는 어떤 경로로도 SMS 한 통도 발송되지 않습니다.
> 이 안전장치는 과거 SMS 대량 오발송 사고(2026-05-17) 이후 도입된 표준 패턴입니다.

---

## 안전장치 (2중 잠금)

| # | 위치 | 기본값 | 운영 방법 |
|---|---|---|---|
| 1 | 코드 상수 `send_engine.MASTER_KILL_SWITCH` | `True` (사고 시) / `False` (일반 운영) | 사용자 `"사고대차 킬스위치 해제"` 정확 문구 시에만 `False` 변경 + push |
| 2 | DB `accident_send_settings(owner).auto_send_enabled` | `false` | ERP UI 의 [자동발송 스위치] ON/OFF 토글 |

(과거 3중 잠금의 send_armed "1번만 발송하기" 게이트는 2026-05-20 사용자 결정으로 폐기.
ON/OFF 단일 토글로 단순화. send_armed 컬럼은 DB 에 남아있으나 코드는 참조하지 않음.)

자동발송 스위치 ON 인 동안:
- 매일 KST 08:30 cron 진입
- 일요일이면 자동 종료 (`SKIP_WEEKDAYS={6}`)
- 미입금 0건이면 종료
- 그 외엔 자동 발송 (사용자 추가 확인 불필요)

---

## 파일 구조

```
send_module/
├── db.py                # Supabase 클라이언트 + KST 헬퍼
├── solapi_sender.py     # HMAC-SHA256 인증 헬퍼
├── send_engine.py       # 미입금 조회 → 그룹핑 → 본문 빌드 → 발송 + 로그 (MASTER_KILL_SWITCH)
├── auto_send.py         # cron 진입점 (자동발송 게이트 + 일요일 제외 + 매일 1회)
├── run.sh               # bash 래퍼 (.env 로드 + venv 활성화)
├── requirements.txt
├── .env.example         # 환경변수 템플릿
└── README.md
```

DB 마이그레이션: `../supabase/migrations/20260519_accident_dokchok.sql`

---

## ENV (`.env`)

```
SUPABASE_URL=https://jjwsnwnfhqcszwmjdcac.supabase.co
SUPABASE_KEY=<service_role_key>     # ⚠ anon 키 X — RLS 우회용 service_role
SOLAPI_API_KEY=...
SOLAPI_API_SECRET=...
SOLAPI_FROM=010-2418-8272           # 사고대차 전용 발신번호
```

**Solapi 발신번호** (사용자 확정 2026-05-19):
- 사고대차 (이 모듈) : `010-2418-8272`
- API_KEY/SECRET 은 회사 공용 Solapi 콘솔 단일 계정 사용

---

## 활성화 절차 (사용자 승인 시점 이후)

1. Supabase 마이그레이션 실행
   ```bash
   psql <SUPABASE_URL> -f supabase/migrations/20260519_accident_dokchok.sql
   ```
   또는 Supabase Studio SQL Editor 에서 붙여넣기 실행.

2. Vultr 서버에 코드 배포
   ```bash
   cd /home/hsh/ && git clone https://github.com/hsh4449/blrent-accidenterp.git
   cd blrent-accidenterp/send_module
   python3 -m venv venv && . venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env && vim .env   # 키 채우기
   ```

3. dry_run 동작 확인 (실 발송 안 됨, MASTER_KILL_SWITCH=True 유지)
   ```bash
   . venv/bin/activate && python3 auto_send.py
   # 출력에 [KILL_SWITCH] MASTER_KILL_SWITCH=True → 강제 dry_run 가 보여야 정상
   ```

4. ERP UI 에서:
   - **자동발송 ON** 토글
   - **본문 템플릿** 확인/수정
   - **cutoff_billing_date** 설정 (예: "이 날짜 이전 청구건은 보내지마")

5. (사용자 `"킬스위치 해제"` 지시 후에만) `send_engine.py` 의 `MASTER_KILL_SWITCH = False` 로 변경 + push,
   Vultr 에서 `git pull`.

6. Vultr crontab 활성화 (처음엔 코멘트 상태)
   ```cron
   30 8 * * * /home/hsh/blrent-accidenterp/send_module/run.sh >> /var/log/accident_auto_send.log 2>&1
   ```

7. ERP UI 의 [1회용 무장] 버튼 클릭. 다음 08:30 cron 에서 1회 발송 후 send_armed 자동 false 복귀.

---

## 본문 템플릿 변수

| 변수 | 치환값 |
|---|---|
| `{insurer}` | 보험사명 (그룹 첫 건) |
| `{manager_name}` | 보험사 담당자명 |
| `{today}` | YYYY-MM-DD |
| `{count}` | 그룹 내 청구건수 |
| `{total}` | 합계 (예: "1,234,567원") |
| `{items_block}` | 각 청구건 상세 (고객명/차량/기간/청구일/금액) |

---

## 트러블슈팅

- **"항상 dry_run 으로 끝남"** → 정상. `MASTER_KILL_SWITCH=True` 또는 `send_armed=false` 게이트.
- **"담당자 번호 없음 N건"** → ERP 에서 보험사 담당자 연락처 입력 필요. 자동발송에선 자동 제외.
- **"같은 사람한테 매일 가야하나?"** → 네, 매일(`SEND_INTERVAL_DAYS=1`). 단 일요일은 `SKIP_WEEKDAYS={6}` 로 자동 제외. 본사/지입 모두 동일.
- **로그 위치** → DB `accident_sms_logs` 테이블 + Vultr `/var/log/accident_auto_send.log`.
