-- blrent-jiip-claims (신동석부장 지입 미입금 자동발송) 폐기에 따른 전용 테이블 제거
-- 2026-05-20 황성현
--
-- 배경:
--   - 지입 사고대차 데이터가 accident_rentals 의 owner='jiip' 으로 통합됨 (사고대차 ERP 사용자모드).
--   - 별도 자동발송 시스템 blrent-jiip-claims 는 완전 폐기 (로컬 폴더 삭제 + GitHub archive).
--   - 사고대차 ERP (이 프로젝트) 는 이 테이블들을 만지지 않음 → DROP 안전.
--
-- 제거 대상 (jiip-claims 전용 6개):
--   jiip_all_claims, jiip_command_queue, jiip_excluded_claims,
--   jiip_settings, jiip_sms_logs, jiip_unpaid_snapshots
--
-- 의존성: FK 관계 없음 (각 테이블 독립). CASCADE 로 view/policy 함께 정리.
-- ============================================================
DROP TABLE IF EXISTS jiip_sms_logs           CASCADE;
DROP TABLE IF EXISTS jiip_unpaid_snapshots   CASCADE;
DROP TABLE IF EXISTS jiip_all_claims         CASCADE;
DROP TABLE IF EXISTS jiip_command_queue      CASCADE;
DROP TABLE IF EXISTS jiip_excluded_claims    CASCADE;
DROP TABLE IF EXISTS jiip_settings           CASCADE;
