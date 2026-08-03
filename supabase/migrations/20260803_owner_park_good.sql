-- 사고대차 ERP: owner 'park'(박민) / 'good'(굿초이스) 추가
-- 2026-08-03 황성현
--
-- 배경:
--   - 신동석(jiip)·김민규(kim) 에 이어 박민(코드 0002)·굿초이스(코드 0003) 를
--     별도 owner 뷰로 같은 ERP 에서 열람/관리 (본사 owner 탭 + 사용자 모드 코드).
--   - 대상 차량은 검사리스트(base_60) 기준: 박민 1대 + 굿초이스 7대.
--
-- owner 값 컨벤션: 'hq' 본사 / 'jiip' 신동석 / 'kim' 김민규 / 'park' 박민 / 'good' 굿초이스
--
-- 발신번호: park/good 은 당분간 hq 발신번호(010-2418-8272) 공유 (kim 과 동일 정책).
--   backend from_number_for() 가 jiip/kim 외 owner 는 SOLAPI_FROM_HQ 로 fallback → 안전.
--   auto_send.py OWNERS=('hq','jiip','kim') 에 park/good 없음 → 자동발송 대상 아님.
--
-- 주의: kim 은 커밋된 마이그레이션 없이 DB 에 직접 적용되어 있어, 현재 각 owner CHECK 는
--   ('hq','jiip','kim') 상태. 아래는 그 위에 park/good 을 더해 재정의한다.

-- ============================================================
-- 1) owner CHECK 제약 확장 (6개 테이블) — 표준 제약명 <table>_owner_check
-- ============================================================
ALTER TABLE accident_rentals            DROP CONSTRAINT IF EXISTS accident_rentals_owner_check;
ALTER TABLE accident_rentals            ADD  CONSTRAINT accident_rentals_owner_check            CHECK (owner IN ('hq','jiip','kim','park','good'));

ALTER TABLE accident_fleet              DROP CONSTRAINT IF EXISTS accident_fleet_owner_check;
ALTER TABLE accident_fleet              ADD  CONSTRAINT accident_fleet_owner_check              CHECK (owner IN ('hq','jiip','kim','park','good'));

ALTER TABLE accident_send_settings      DROP CONSTRAINT IF EXISTS accident_send_settings_owner_check;
ALTER TABLE accident_send_settings      ADD  CONSTRAINT accident_send_settings_owner_check      CHECK (owner IN ('hq','jiip','kim','park','good'));

ALTER TABLE accident_sms_logs           DROP CONSTRAINT IF EXISTS accident_sms_logs_owner_check;
ALTER TABLE accident_sms_logs           ADD  CONSTRAINT accident_sms_logs_owner_check           CHECK (owner IN ('hq','jiip','kim','park','good'));

ALTER TABLE accident_excluded_contracts DROP CONSTRAINT IF EXISTS accident_excluded_contracts_owner_check;
ALTER TABLE accident_excluded_contracts ADD  CONSTRAINT accident_excluded_contracts_owner_check CHECK (owner IN ('hq','jiip','kim','park','good'));

ALTER TABLE accident_manual_send_queue  DROP CONSTRAINT IF EXISTS accident_manual_send_queue_owner_check;
ALTER TABLE accident_manual_send_queue  ADD  CONSTRAINT accident_manual_send_queue_owner_check  CHECK (owner IN ('hq','jiip','kim','park','good'));

-- ============================================================
-- 2) accident_send_settings — park/good 행 시드 (auto OFF, armed OFF)
--    id 컬럼은 legacy CHECK(id=1) 이라 1 고정. message_template 은 컬럼 default 사용.
-- ============================================================
INSERT INTO accident_send_settings (id, owner, auto_send_enabled, send_armed)
VALUES (1, 'park', false, false),
       (1, 'good', false, false)
ON CONFLICT (owner) DO NOTHING;

-- ============================================================
-- 3) accident_fleet — 박민 1대 + 굿초이스 7대 (검사리스트 기준)
-- ============================================================
INSERT INTO accident_fleet (vehicle_number, model, status, owner, note, updated_at)
VALUES
    ('106호4072', 'S클래스 S580L 4MATIC', 'active', 'park', NULL, NOW()),
    ('106호5152', '쏘렌토',              'active', 'good', NULL, NOW()),
    ('106호5154', 'K8',                  'active', 'good', NULL, NOW()),
    ('07호8264',  '아이오닉 5',          'active', 'good', NULL, NOW()),
    ('106호8679', '아반떼',              'active', 'good', NULL, NOW()),
    ('07호8214',  '아이오닉 5',          'active', 'good', NULL, NOW()),
    ('106호3392', 'K8',                  'active', 'good', NULL, NOW()),
    ('106호6098', 'G80',                 'active', 'good', NULL, NOW())
ON CONFLICT (vehicle_number) DO UPDATE
SET owner = EXCLUDED.owner,
    model = EXCLUDED.model,
    status = EXCLUDED.status,
    updated_at = NOW();
