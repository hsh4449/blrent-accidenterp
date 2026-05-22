"""
수동 발송 큐 처리기 — Vultr cron `*/2 * * * *` 로 호출.

흐름:
  1) accident_manual_send_queue 에서 processed_at IS NULL 인 행 가장 오래된 순으로 가져옴
  2) 각 행마다:
     - owner 의 미입금 contracts 로드
     - target_phones 필터 적용 (NULL/빈 배열이면 전체)
     - send_plan(dry_run=False, trigger_type='manual') 으로 발송
     - processed_at + result JSON 기록
  3) 미처리 큐 0건이면 조용히 종료 (cron 로그 부담 최소화)

게이트:
  - SEND_INTERVAL_DAYS (auto 전용) 무시 — 수동은 사용자 의도로 매번 가능
  - 일요일 제한 무시 — 수동은 사용자가 직접 누른 거라 의도된 발송
  - MASTER_KILL_SWITCH 는 send_plan 내부에서 여전히 작동
"""
from datetime import datetime
import sys

from db import get_client, KST
from send_engine import (
    load_unpaid_contracts,
    load_excluded_ids,
    build_message_plan,
    send_plan,
)


MAX_PROCESS_PER_RUN = 20  # 한 번 cron 에서 최대 20건 처리 (안전 한도)


def process_one(sb, row) -> dict:
    """큐 1건 처리 → result dict 반환."""
    owner = row['owner']
    target_phones = row.get('target_phones') or None
    if target_phones == []:
        target_phones = None  # 빈 배열 = 전체

    unpaid = load_unpaid_contracts(sb, owner)
    excluded = load_excluded_ids(sb, owner)

    plan = build_message_plan(sb, owner, unpaid, excluded, target_phones=target_phones)

    if not plan['messages']:
        return {
            'sent': 0, 'failed': 0, 'count': 0,
            'note': f'발송 대상 0건 (owner={owner}, target_phones={target_phones})',
        }

    result = send_plan(
        plan, owner=owner, dry_run=False,
        trigger_type='manual',
        triggered_by=f"manual_queue:#{row['id']}:{row.get('requested_by') or 'ui'}",
        sb=sb,
    )
    return result


def main():
    sb = get_client()
    rows = (sb.table('accident_manual_send_queue').select('*')
              .is_('processed_at', 'null')
              .order('requested_at', desc=False)
              .limit(MAX_PROCESS_PER_RUN)
              .execute().data) or []

    if not rows:
        return  # 조용히 종료

    print(f'=== MANUAL_QUEUE 시작 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST · {len(rows)}건 ===')

    for row in rows:
        try:
            result = process_one(sb, row)
            sb.table('accident_manual_send_queue').update({
                'processed_at': datetime.now(KST).isoformat(),
                'result': result,
            }).eq('id', row['id']).execute()
            print(f"  [#{row['id']}] owner={row['owner']} target={row.get('target_phones')} → {result}")
        except Exception as e:
            err_result = {'error': f'{type(e).__name__}: {e}'}
            try:
                sb.table('accident_manual_send_queue').update({
                    'processed_at': datetime.now(KST).isoformat(),
                    'result': err_result,
                }).eq('id', row['id']).execute()
            except Exception:
                pass
            print(f"  [#{row['id']}] ERROR: {e}", file=sys.stderr)

    print(f'=== MANUAL_QUEUE 완료 {datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")} KST ===')


if __name__ == '__main__':
    main()
