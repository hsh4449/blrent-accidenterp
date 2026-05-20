-- 사고대차 ERP: 본사/지입 데이터 분리를 위한 owner 컬럼 추가
-- 2026-05-20 황성현
--
-- 배경:
--   - 본사 사고대차 14대(crawler.py VEHICLE_NUMBERS) 외에
--     신동석부장 지입 17대를 별도 영역으로 같은 ERP에서 보이도록 통합.
--   - 본사모드(기본): owner='hq' 또는 null만 표시
--   - 사용자모드(코드 0000): owner='jiip'만 표시
--
-- owner 값 컨벤션:
--   'hq'   = 본사 (기본값). 기존 데이터 전부 이 값으로 backfill.
--   'jiip' = 신동석부장 지입차.
--
-- ============================================================
-- 1) accident_rentals.owner
-- ============================================================
ALTER TABLE accident_rentals
    ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'hq'
        CHECK (owner IN ('hq','jiip'));

CREATE INDEX IF NOT EXISTS idx_accident_rentals_owner
    ON accident_rentals(owner);

-- ============================================================
-- 2) accident_fleet.owner
-- ============================================================
ALTER TABLE accident_fleet
    ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT 'hq'
        CHECK (owner IN ('hq','jiip'));

CREATE INDEX IF NOT EXISTS idx_accident_fleet_owner
    ON accident_fleet(owner);

-- ============================================================
-- 3) 신동석부장 지입차 17대 fleet 시드 (active)
--    차종은 blrent-car-system vehicles 화면에서 그대로 가져온 값.
--    이후 crawler/UI에서 수정 가능.
-- ============================================================
INSERT INTO accident_fleet (vehicle_number, model, status, owner, note, updated_at)
VALUES
    ('154하4286', '박스터 718 4.0 GTS',                 'active', 'jiip', NULL, NOW()),
    ('106호8993', 'Mercedes-Benz E 200',                'active', 'jiip', NULL, NOW()),
    ('106호9194', 'Mustang 2.3L Convertible',           'active', 'jiip', NULL, NOW()),
    ('106호9256', '더 뉴아반떼 N 가솔린 2.0 터보 N DCT', 'active', 'jiip', NULL, NOW()),
    ('106호9334', 'Mercedes-Benz GLE 4504MATIC',        'active', 'jiip', NULL, NOW()),
    ('106호9340', 'Mercedes-Benz CLE 200 Cabriolet',    'active', 'jiip', NULL, NOW()),
    ('106호9341', '뉴 GV80 가솔린 2.5',                'active', 'jiip', NULL, NOW()),
    ('106호9388', 'BMW X6 xDrive30d M Sport',           'active', 'jiip', NULL, NOW()),
    ('106호9433', 'BMW 520i',                           'active', 'jiip', NULL, NOW()),
    ('106호9558', 'BMW 520i',                           'active', 'jiip', NULL, NOW()),
    ('07호8433',  '테슬라 모델Y',                       'active', 'jiip', NULL, NOW()),
    ('106호9666', '벤츠 E200',                          'active', 'jiip', NULL, NOW()),
    ('106호9759', 'BMW X5 xDrive30d',                   'active', 'jiip', NULL, NOW()),
    ('106호9755', 'Mercedes-Benz E200',                 'active', 'jiip', NULL, NOW()),
    ('106호9754', 'Mercedes-Benz E200',                 'active', 'jiip', NULL, NOW()),
    ('106호9878', 'Mercedes-Benz GLC 220d 4MATIC',      'active', 'jiip', NULL, NOW()),
    ('106호9893', 'Mercedes-Benz E 200',                'active', 'jiip', NULL, NOW())
ON CONFLICT (vehicle_number) DO UPDATE
SET owner = EXCLUDED.owner,
    model = EXCLUDED.model,
    status = EXCLUDED.status,
    updated_at = NOW();
