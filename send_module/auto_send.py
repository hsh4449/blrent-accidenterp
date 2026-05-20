"""
자동발송 cron 진입점. 매일 KST 08:30 호출.

owner 별 (본사 'hq', 지입 'jiip') 독립 실행 — 게이트/마지막발송일/발신번호 분리.

게이트 순서 (각 owner 마다 동일 적용):
  1. accident_send_settings(owner).auto_send_enabled = false → 해당 owner 종료
  2. last_auto_send_date 기준 SEND_INTERVAL_DAYS 미경과 → 종료
  3. 미입금 0건 → 종료
  4. send_engine.send_plan(owner=...) 호출 (내부 MASTER_KILL_SWITCH + send_armed 재확인)
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

# 자동발송 도메인 — 본사 hq 와 지입 jiip 둘 다 매일 cron 에서 순차 평가.
# 각 owner 의 settings/last_auto_send_date/send_armed/발신번호가 독립적으로 작동.
OWNERS = ('hq', 'jiip')


def run_one(sb, owner: str, today) -> None:
    print(f'\n--- [owner={owner}] 평가 시작 ---')
    settings = (sb.table('accident_send_settings').select('*')
                  .eq('owner', owner).single().execute().data)
    if not settings or not settings.get('auto_send_enabled'):
        print(f'[GATE owner={owner}] auto_send_enabled = false → 종료')
        return

    last = settings.get('last_auto_send_date')
    if last:
        last_date = datetime.strptime(str(last), '%Y-%m-%d').date()
        days_since = (today - last_date).days
        if days_since < SEND_INTERVAL_DAYS:
            print(f'[GATE owner={owner}] 마지막 자동발송 {last_date} ({days_since}일 전) → '
                  f'최소 간격 {SEND_INTERVAL_DAYS}일 미달, 종료')
            return

    unpaid = load_unpaid_contracts(sb, owner)
    excluded = load_excluded_ids(sb, owner)
    print(f'[1 owner={owner}] 미입금 {len(unpaid)}건 / 제외 {len(excluded)}건')

    plan = build_message_plan(sb, owner, unpaid, excluded)
    print(f'[2 owner={owner}] 발송 대상 담당자 {len(plan["messages"])}명, '
          f'연락처없음 {plan["no_contact_count"]}건')

    if not plan['messages']:
        print(f'[GATE owner={owner}] 발송 대상 0건 → 종료')
        return

    result = send_plan(plan, owner=owner, dry_run=False,
                      trigger_type='auto', triggered_by='cron', sb=sb)
    print(f'[3 owner={owner}] 결과: {result}')

    if result.get('sent', 0) > 0:
        sb.table('accident_send_settings').update({
            'last_auto_send_date': today.isoformat(),
            'updated_at': datetime.now(KST).isoformat(),
            'updated_by': 'cron:auto_send',
        }).eq('owner', owner).execute()
        next_day = (today + timedelta(days=SEND_INTERVAL_DAYS)).isoformat()
        print(f'[4 owner={owner}] last_auto_send_date = {today.isoformat()} (다음 가능일 {next_day})')


def main():
    started = datetime.now(KST)
    print(f'=== ACCIDENT AUTO_SEND 시작 {started.strftime("%Y-%m-%d %H:%M:%S")} KST ===')

    sb = get_client()
    today = kst_today()

    for owner in OWNERS:
        try:
            run_one(sb, owner, today)
        except Exception as e:
            # 한 owner 실패가 다른 owner 발송을 막지 않도록 격리.
            print(f'[ERROR owner={owner}] {type(e).__name__}: {e}')

    print(f'=== ACCIDENT AUTO_SEND 완료 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST ===')


if __name__ == '__main__':
    main()
