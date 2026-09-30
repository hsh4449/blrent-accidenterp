-- 2026-09-30 적용 완료 (Management API). 독촉 대상을 계약 시작일 기준으로 제한하는 칸 추가.
alter table accident_send_settings add column if not exists cutoff_start_date date;
comment on column accident_send_settings.cutoff_start_date is '이 날짜 이전에 시작(start_date)한 계약은 독촉 제외. 2026-09-30 사용자 지시: 본사·신동석 제외 발송 담당자 전원 2026-08-01';
update accident_send_settings set cutoff_start_date='2026-08-01', updated_at=now(), updated_by='hsh:2026-09-30 8/1 계약 기준'
 where owner in ('jang','kim','choi','jeon','yu','kang','han');
