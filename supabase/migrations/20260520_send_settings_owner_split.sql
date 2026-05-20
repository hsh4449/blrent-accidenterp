-- 사고대차 미입금 자동 독촉 SMS: 본사(hq) / 지입(jiip) 분리
-- 2026-05-20 황성현
--
-- 배경:
--   - 사용자모드(코드 4417, 신동석 지입차) 도입으로 본사/지입 데이터 분리됨.
--   - 발송 설정/로그/제외목록도 본사와 지입이 섞여서 본사 발송이 지입 "마지막 발송"
--     으로 잡히는 문제 발생. 두 도메인이 독립적으로 운영되어야 함.
--
-- 변경:
--   1) accident_send_settings: id PK 제거 → owner PK. id=1 row 를 owner='hq' 로 보존
--      + owner='jiip' row 시드 (auto OFF 상태).
--   2) accident_sms_logs / accident_excluded_contracts: owner 컬럼 추가 (default 'hq').
--   3) 인덱스 추가.

-- ============================================================
-- 1) accident_send_settings — id PK → owner PK
-- ============================================================
ALTER TABLE accident_send_settings
    ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'hq'
        CHECK (owner IN ('hq','jiip'));

-- 기존 PK 제거 후 owner PK 로 교체. id 컬럼은 legacy 호환용으로 유지(1).
ALTER TABLE accident_send_settings DROP CONSTRAINT IF EXISTS accident_send_settings_pkey;
ALTER TABLE accident_send_settings ADD PRIMARY KEY (owner);

-- 지입 row 시드 (기본값으로 — auto_send 꺼짐, send_armed 꺼짐).
INSERT INTO accident_send_settings (id, owner, auto_send_enabled, send_armed)
VALUES (1, 'jiip', false, false)
ON CONFLICT (owner) DO NOTHING;

-- ============================================================
-- 2) accident_sms_logs — owner 컬럼
-- ============================================================
ALTER TABLE accident_sms_logs
    ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'hq'
        CHECK (owner IN ('hq','jiip'));

CREATE INDEX IF NOT EXISTS idx_accident_sms_logs_owner_sent_at
    ON accident_sms_logs(owner, sent_at DESC);

-- ============================================================
-- 3) accident_excluded_contracts — owner 컬럼
-- ============================================================
ALTER TABLE accident_excluded_contracts
    ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'hq'
        CHECK (owner IN ('hq','jiip'));

CREATE INDEX IF NOT EXISTS idx_accident_excluded_contracts_owner
    ON accident_excluded_contracts(owner);
