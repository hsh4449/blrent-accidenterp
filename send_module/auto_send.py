"""
자동발송 cron 진입점. 매일 KST 08:30 호출.

게이트 순서:
  1. accident_send_settings.auto_send_enabled = false → 종료
  2. last_auto_send_date 기준 SEND_INTERVAL_DAYS 미경과 → 종료
  3. 미입금 0건 → 종료
  4. send_engine.send_plan 호출 (내부에서 MASTER_KILL_SWITCH + send_armed 재확인)
"""
from datetime import datetime, timedelta

from db import get_client, kst_today, KST
from send_engine import (
    load_unpaid_contracts,
    load_excluded_ids,
    build_message_plan,
    send_plan,
)

# 같은 보험사 담당자 재발송 최소 간격 (일)
SEND_INTERVAL_DAYS = 3


def main():
    started = datetime.now(KST)
    print(f'=== ACCIDENT AUTO_SEND 시작 {started.strftime("%Y-%m-%d %H:%M:%S")} KST ===')

    sb = get_client()
    settings = sb.table('accident_send_settings').select('*').eq('id', 1).single().execute().data

    if not settings.get('auto_send_enabled'):
        print('[GATE] auto_send_enabled = false → 종료')
        return

    today = kst_today()
    last = settings.get('last_auto_send_date')
    if last:
        last_date = datetime.strptime(str(last), '%Y-%m-%d').date()
        days_since = (today - last_date).days
        if days_since < SEND_INTERVAL_DAYS:
            print(f'[GATE] 마지막 자동발송 {last_date} ({days_since}일 전) → '
                  f'최소 간격 {SEND_INTERVAL_DAYS}일 미달, 종료')
            return

    unpaid = load_unpaid_contracts(sb)
    excluded = load_excluded_ids(sb)
    print(f'[1] 미입금 {len(unpaid)}건 / 제외 {len(excluded)}건')

    plan = build_message_plan(sb, unpaid, excluded)
    print(f'[2] 발송 대상 담당자 {len(plan["messages"])}명, '
          f'연락처없음 {plan["no_contact_count"]}건')

    if not plan['messages']:
        print('[GATE] 발송 대상 0건 → 종료')
        return

    result = send_plan(plan, dry_run=False, trigger_type='auto', triggered_by='cron', sb=sb)
    print(f'[3] 결과: {result}')

    if result.get('sent', 0) > 0:
        sb.table('accident_send_settings').update({
            'last_auto_send_date': today.isoformat(),
            'updated_at': datetime.now(KST).isoformat(),
            'updated_by': 'cron:auto_send',
        }).eq('id', 1).execute()
        next_day = (today + timedelta(days=SEND_INTERVAL_DAYS)).isoformat()
        print(f'[4] last_auto_send_date = {today.isoformat()} (다음 가능일 {next_day})')

    print(f'=== ACCIDENT AUTO_SEND 완료 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST ===')


if __name__ == '__main__':
    main()
