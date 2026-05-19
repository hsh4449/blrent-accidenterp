"""
사고대차 미입금 독촉 발송 엔진.

흐름:
  contracts(status='청구완료' AND deposit_date IS NULL) → cutoff 적용 →
  excluded 제외 → 보험사 담당자별 그룹핑 → 본문 빌드 → 솔라피 발송 → 로그 기록.

dry_run=True 면 실제 발송 없이 페이로드만 반환 (사고 방지).

============================================================
설계 기반: blrent-jiip-claims/send_engine.py
2026-05-17 지입 SMS 154통 오발송 사고 이후 동일 3중 잠금 패턴.
============================================================
"""
from collections import defaultdict
from datetime import datetime, timedelta

import requests

from db import get_client, KST
from solapi_sender import _auth_header, byte_len, API_URL, SOLAPI_FROM


# 청구일로부터 N일 이상 경과한 건만 자동발송 대상
# (ERP UI DokchokTab.MIN_OVERDUE_DAYS 와 같은 값 유지)
MIN_OVERDUE_DAYS = 3


# ─────────────────────────────────────────────────────────────────────────
# 🚨 MASTER KILL SWITCH 🚨
#
# True 인 동안 모든 발송 경로(자동/수동/테스트)가 dry_run 으로 강제 전환됩니다.
#
# 해제 trigger phrase (2026-05-19 강화 — 지입 시스템과 분리):
#   사용자(황성현) 메시지에 정확히 "사고대차 킬스위치 해제" 라는 문구가
#   포함되어 있어야만 False 로 변경 + git push 가능.
#   "킬스위치 해제" 단독은 모호하므로 거부 (jiip 시스템과 충돌 가능성).
#   "풀어줘", "해제해", "OFF" 등 다른 표현 모두 거부.
#
# 풀린 후에도 accident_send_settings.send_armed 게이트 + 사용자 클릭 확인이 추가로 필요합니다.
# 발송 1회 끝나면 즉시 MASTER_KILL_SWITCH=True 로 복귀 + push.
# ─────────────────────────────────────────────────────────────────────────
MASTER_KILL_SWITCH = True


def fmt_won(n) -> str:
    return f'{int(n or 0):,}원'


# ============================================================
# 1) 미입금 대상 로드
# ============================================================
def load_unpaid_contracts(sb):
    """
    accident_rentals 테이블에서 미입금 대상 조회.
    조건:
      - status = '청구완료'
      - deposit_date IS NULL
      - is_deleted != true  (휴지통 제외)
      - billing_date <= today - MIN_OVERDUE_DAYS  (청구 후 N일 이상 경과)
      - billing_date >= cutoff (설정 시, 옛 청구건 제외)
    """
    settings = sb.table('accident_send_settings').select(
        'cutoff_billing_date'
    ).eq('id', 1).single().execute().data
    cutoff = settings.get('cutoff_billing_date') if settings else None

    today = datetime.now(KST).date()
    overdue_threshold = (today - timedelta(days=MIN_OVERDUE_DAYS)).isoformat()

    q = (sb.table('accident_rentals').select('*')
         .eq('status', '청구완료')
         .is_('deposit_date', 'null')
         .neq('is_deleted', True)
         .lte('billing_date', overdue_threshold))
    if cutoff:
        q = q.gte('billing_date', cutoff)
    return q.execute().data or []


def load_excluded_ids(sb) -> set:
    rows = sb.table('accident_excluded_contracts').select('contract_id').execute().data or []
    return {r['contract_id'] for r in rows}


# ============================================================
# 2) 본문 빌드
# ============================================================
def _items_block(items: list) -> str:
    """담당자에게 보낼 청구건 상세 블록 (사용자 지정 포맷 2026-05-19)"""
    blocks = []
    for i, c in enumerate(items, 1):
        block = [
            f'{i}) {c.get("customer_number") or "-"}/ {c.get("customer_vehicle") or "-"}/ {c.get("customer_name") or "-"}',
            f'접수번호 {c.get("receipt_no") or "-"}',
            f'대여기간 {c.get("start_date") or "?"} ~ {c.get("end_date") or "?"}',
            f'청구일 {c.get("billing_date") or "-"}',
            f'청구액 {fmt_won(c.get("billing_amount", 0))}',
        ]
        blocks.append('\n'.join(block))
    return '\n\n'.join(blocks)


def build_manager_message(template: str, items: list) -> tuple[str, int]:
    today = datetime.now(KST).strftime('%Y-%m-%d')
    insurer = items[0].get('insurer') or ''
    manager_name = items[0].get('insurance_manager_name') or '담당자'
    total = sum(int(c.get('billing_amount') or 0) for c in items)

    text = (template or '')
    text = text.replace('{insurer}', insurer)
    text = text.replace('{manager_name}', manager_name)
    text = text.replace('{today}', today)
    text = text.replace('{count}', str(len(items)))
    text = text.replace('{total}', fmt_won(total))
    text = text.replace('{items_block}', _items_block(items))
    return text, total


# ============================================================
# 3) 발송 계획 (실 발송 안 함)
# ============================================================
def build_message_plan(sb, contracts: list, excluded_ids: set,
                       *, target_phones=None, target_contract_ids=None) -> dict:
    """
    Returns: {
        'messages': [{phone, recipient_name, insurer, contract_ids, total_amount, text, msg_type}, ...],
        'no_contact_count': int,
        'no_contact_items': [...]
    }
    """
    settings = sb.table('accident_send_settings').select(
        'message_template'
    ).eq('id', 1).single().execute().data
    template = (settings or {}).get('message_template') or ''

    pool = [c for c in contracts if c['id'] not in excluded_ids]
    if target_contract_ids is not None:
        pool = [c for c in pool if c['id'] in set(target_contract_ids)]

    groups = defaultdict(list)
    no_contact = []
    for c in pool:
        phone = (c.get('insurance_manager_phone') or '').replace('-', '').strip()
        if not phone:
            no_contact.append(c)
            continue
        groups[phone].append(c)

    if target_phones is not None:
        wanted = {p.replace('-', '').strip() for p in target_phones}
        groups = {k: v for k, v in groups.items() if k in wanted}

    messages = []
    for phone, items in groups.items():
        text, total = build_manager_message(template, items)
        messages.append({
            'phone': phone,
            'recipient_name': items[0].get('insurance_manager_name'),
            'insurer': items[0].get('insurer'),
            'contract_ids': [it['id'] for it in items],
            'total_amount': total,
            'text': text,
            'msg_type': 'LMS' if byte_len(text) > 90 else 'SMS',
        })

    return {
        'messages': messages,
        'no_contact_count': len(no_contact),
        'no_contact_items': no_contact,
    }


# ============================================================
# 4) 실 발송 (게이트 통과 후만)
# ============================================================
def _post_solapi(payload_msgs: list) -> tuple[int, dict]:
    headers = {'Authorization': _auth_header(), 'Content-Type': 'application/json'}
    resp = requests.post(API_URL, json={'messages': payload_msgs}, headers=headers, timeout=30)
    try:
        body = resp.json()
    except Exception:
        body = {'raw': resp.text[:1000]}
    return resp.status_code, body


def send_plan(plan: dict, *, dry_run: bool, trigger_type: str, triggered_by: str, sb=None) -> dict:
    """
    plan 의 messages 를 실제 발송 + 로그.

    안전 게이트 순서:
        1. 메시지 0건이면 즉시 종료
        2. MASTER_KILL_SWITCH=True → 강제 dry_run
        3. accident_send_settings.send_armed=false → 강제 dry_run
        4. 실 발송 → 솔라피 호출 → 로그 → send_armed 자동 false 복귀
    """
    if sb is None:
        sb = get_client()

    msgs = list(plan.get('messages') or [])
    if not msgs:
        return {'sent': 0, 'failed': 0, 'dry_run': dry_run, 'count': 0}

    # 1차: MASTER KILL SWITCH
    if MASTER_KILL_SWITCH and not dry_run:
        print(f'[KILL_SWITCH] MASTER_KILL_SWITCH=True → 강제 dry_run (triggered_by={triggered_by})')
        dry_run = True

    # 2차: DB 게이트 (1회용 무장)
    armed = False
    if not dry_run:
        s = sb.table('accident_send_settings').select(
            'send_armed,armed_by'
        ).eq('id', 1).single().execute().data
        armed = bool((s or {}).get('send_armed'))
        if not armed:
            print(f'[LOCK] send_armed=false → 강제 dry_run (triggered_by={triggered_by})')
            dry_run = True

    if dry_run:
        return {
            'sent': 0, 'failed': 0, 'dry_run': True,
            'count': len(msgs), 'messages': msgs,
            'blocked_by_kill_switch': MASTER_KILL_SWITCH,
            'blocked_by_lock': (not armed and not MASTER_KILL_SWITCH),
        }

    # 실 발송
    payload_msgs = []
    for m in msgs:
        pm = {
            'to': m['phone'],
            'from': SOLAPI_FROM.replace('-', ''),
            'text': m['text'],
            'type': m['msg_type'],
        }
        if m['msg_type'] == 'LMS':
            pm['subject'] = '[비엘렌터카] 사고대차 미입금 안내'
        payload_msgs.append(pm)

    status_code, body = _post_solapi(payload_msgs)
    print(f'[SOLAPI] HTTP {status_code}')

    sent = failed = 0
    if status_code < 400:
        cnt = (body.get('groupInfo') or {}).get('count') or {}
        sent = cnt.get('registeredSuccess', 0)
        failed = cnt.get('registeredFailed', 0)
    else:
        failed = len(msgs)

    group_id = body.get('groupId')
    log_rows = []
    for m in msgs:
        log_rows.append({
            'trigger_type': trigger_type,
            'triggered_by': triggered_by,
            'recipient_phone': m['phone'],
            'recipient_name': m['recipient_name'],
            'insurer': m['insurer'],
            'contract_ids': m['contract_ids'],
            'total_amount': m['total_amount'],
            'message_type': m['msg_type'],
            'message_text': m['text'],
            'solapi_message_id': group_id,
            'solapi_status_code': status_code,
            'solapi_response': body if status_code >= 400 else None,
        })
    if log_rows:
        sb.table('accident_sms_logs').insert(log_rows).execute()

    # 1회용 무장 자동 해제 (실 발송 시도 직후, 성공/실패 무관)
    sb.table('accident_send_settings').update({
        'send_armed': False,
        'armed_at': None,
        'armed_by': None,
        'updated_at': datetime.now(KST).isoformat(),
        'updated_by': f'auto-disarm:{triggered_by}',
    }).eq('id', 1).execute()
    print('[LOCK] 발송 후 send_armed 자동 false 복귀')

    return {
        'sent': sent, 'failed': failed,
        'count': len(msgs), 'group_id': group_id, 'status_code': status_code,
    }
