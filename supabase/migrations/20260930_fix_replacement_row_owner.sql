-- 2026-09-30 적용 완료 (Management API). 교체건 부 차량 행 owner 정정 12건 — 메인 행 owner 를 복사해 들어가 있던 것.
-- 2674366(9340, 2025-11 청구)은 부 행이 아니라 메인 행이고 신동석 수집 기준일(2025-12-20) 이전이라 본사 유지.
update accident_rentals r set owner = v.new_owner, updated_at = now()
from (values
 ('2547514','park','hq'), ('2778644','kim','park'), ('2850372','park','kim'),
 ('2855601','jiip','hq'), ('2857144','jiip','hq'), ('2951287','kim','park'),
 ('2971367','kim','hq'), ('3209556','choi','kim'), ('3214668','jang','jiip'),
 ('3214901','jeon','kim'), ('3230524','kim','choi'), ('3279235','kim','jiip')
) as v(id, old_owner, new_owner)
where r.id = v.id and r.owner = v.old_owner
returning r.id, r.vehicle_number, r.owner;
