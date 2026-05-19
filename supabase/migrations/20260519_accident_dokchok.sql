-- 사고대차 미입금 자동 독촉 SMS 시스템 (blrent-accidenterp)
-- 2026-05-19 황성현
--
-- 설계 출처: blrent-jiip-claims (지입 미입금 시스템 2026-05-17 사고 이후 패턴)
-- 안전장치 3중:
--   1) 코드 상수 send_engine.MASTER_KILL_SWITCH = True
--   2) DB accident_send_settings.send_armed (1회용 무장)
--   3) Vultr crontab 라인 LOCKED-BY-USER 코멘트
--
-- ============================================================
-- 1) 단일 행 설정 테이블 (id=1 only)
-- ============================================================
CREATE TABLE IF NOT EXISTS accident_send_settings (
    id                    SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),

    -- ON/OFF
    auto_send_enabled     BOOLEAN  NOT NULL DEFAULT false,

    -- 1회용 무장 (실발송 직후 false 자동 복귀)
    send_armed            BOOLEAN  NOT NULL DEFAULT false,
    armed_at              TIMESTAMPTZ,
    armed_by              TEXT,

    -- 이 청구일 이전 건은 자동발송 제외 (수동 cutoff)
    cutoff_billing_date   DATE,

    -- 발송 본문 템플릿 (변수: {insurer}, {manager_name}, {today}, {items_block}, {total}, {count})
    message_template      TEXT NOT NULL DEFAULT
$tpl$[{insurer} {manager_name}님께] 사고대차 미입금 안내 ({today})

아래 {count}건의 사고대차 청구건이 입금 확인되지 않아 안내드립니다.

{items_block}

합계 {total}
빠른 입금 처리 부탁드립니다. 감사합니다.
- 비엘렌터카$tpl$,

    -- 마지막 자동발송일 (3일 간격 게이트용)
    last_auto_send_date   DATE,

    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            TEXT
);

-- 최초 1회 시드 (이미 있으면 무시)
INSERT INTO accident_send_settings (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;


-- ============================================================
-- 2) 발송 로그 (모든 발송 시도 1통씩 1행)
-- ============================================================
CREATE TABLE IF NOT EXISTS accident_sms_logs (
    id                    BIGSERIAL PRIMARY KEY,
    sent_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    trigger_type          TEXT NOT NULL,             -- 'auto' | 'manual' | 'preview'
    triggered_by          TEXT,                       -- 'cron' | 'user:hsh' 등

    recipient_phone       TEXT NOT NULL,
    recipient_name        TEXT,
    insurer               TEXT,

    contract_ids          TEXT[] NOT NULL,            -- 포함된 contracts.id 배열
    total_amount          BIGINT NOT NULL DEFAULT 0,

    message_type          TEXT NOT NULL,              -- 'SMS' | 'LMS'
    message_text          TEXT NOT NULL,

    -- 솔라피 응답
    solapi_message_id     TEXT,
    solapi_status_code    INT,
    solapi_response       JSONB                       -- 실패 시에만 저장
);

CREATE INDEX IF NOT EXISTS idx_accident_sms_logs_sent_at
    ON accident_sms_logs (sent_at DESC);


-- ============================================================
-- 3) 발송 제외 목록 (수동으로 "이 건은 보내지마" 표시)
-- ============================================================
CREATE TABLE IF NOT EXISTS accident_excluded_contracts (
    contract_id           TEXT PRIMARY KEY,
    excluded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    excluded_by           TEXT,
    reason                TEXT
);


-- ============================================================
-- RLS — 끄고 운영 (의도된 정책, 사용자 2026-05-19 명시)
-- 사유: 외부인 사용 X, 사용자 3~4명 수준, 하드코딩은 본인만.
-- 기존 jiip_*/accident_rentals 등과 동일 패턴으로 통일.
-- ============================================================
ALTER TABLE accident_send_settings       DISABLE ROW LEVEL SECURITY;
ALTER TABLE accident_sms_logs            DISABLE ROW LEVEL SECURITY;
ALTER TABLE accident_excluded_contracts  DISABLE ROW LEVEL SECURITY;
