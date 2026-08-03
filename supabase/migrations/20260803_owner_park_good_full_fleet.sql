-- 사고대차 ERP: 박민(park)/굿초이스(good) fleet 전체 반영
-- 2026-08-03 황성현
--
-- 배경: 최초(20260803_owner_park_good.sql)엔 검사리스트(base_60) 기준으로 박민 1 / 굿초이스 7
--   만 넣었으나, 검사리스트는 검사대상 일부만 담겨 실제 대수와 달랐음. 정본은
--   blrent-car-system vehicles.customer_name (같은 Supabase 프로젝트) 기준:
--     - 박민소장 19대 (단, 106호9315 는 김민규 유지 결정 → 제외, 실질 18대)
--     - 굿초이스(송지연 주임) 23대
--     - 박민정(106호8003)은 다른 사람 → 제외
--
-- model 은 vehicles 에서 그대로 가져온다(수기 입력 X). 이미 있으면 owner/model 갱신.

-- 박민(park) 18대 — 박민소장 정확일치, 9315 제외
INSERT INTO accident_fleet (vehicle_number, model, status, owner, updated_at)
SELECT car_number, model, 'active', 'park', NOW()
FROM vehicles
WHERE coalesce(is_deleted,false)=false AND customer_name = '박민소장' AND car_number <> '106호9315'
ON CONFLICT (vehicle_number) DO UPDATE
SET owner = EXCLUDED.owner, model = EXCLUDED.model, updated_at = NOW();

-- 굿초이스(good) 23대
INSERT INTO accident_fleet (vehicle_number, model, status, owner, updated_at)
SELECT car_number, model, 'active', 'good', NOW()
FROM vehicles
WHERE coalesce(is_deleted,false)=false AND customer_name LIKE '%굿초이스%'
ON CONFLICT (vehicle_number) DO UPDATE
SET owner = EXCLUDED.owner, model = EXCLUDED.model, updated_at = NOW();
