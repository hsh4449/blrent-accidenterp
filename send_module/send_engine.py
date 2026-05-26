"""
사고대차 미입금 독촉 발송 엔진.

흐름:
  contracts(status='청구완료' AND deposit_date IS NULL) → cutoff 적용 →
  excluded 제외 → 보험사 담당자별 그룹핑 → 본문 빌드 → 솔라피 발송 → 로그 기록.

dry_run=True 면 실제 발송 없이 페이로드만 반환 (사고 방지).

============================================================
설계: 2026-05-17 SMS 대량 오발송 사고 이후 도입한 3중 잠금 패턴.
============================================================
"""
from collections import defaultdict
from datetime import datetime, timedelta

import requests

from db import get_client, KST
from solapi_sender import _auth_header, byte_len, API_URL, from_number_for


# 청구일로부터 N일 이상 경과한 건만 자동발송 대상
# (ERP UI DokchokTab.MIN_OVERDUE_DAYS 와 같은 값 유지)
# 1 = 청구 다음날 08:30 cron 부터 발송 가능 (2026-05-22 사용자 변경: 3 → 1)
MIN_OVERDUE_DAYS = 1


# ─────────────────────────────────────────────────────────────────────────
# 🚨 MASTER KILL SWITCH 🚨
#
# True 인 동안 모든 발송 경로(자동/수동/테스트)가 dry_run 으로 강제 전환됩니다.
#
# 해제 trigger phrase:
#   사용자(황성현) 메시지에 정확히 "사고대차 킬스위치 해제" 라는 문구가
#   포함되어 있어야만 False 로 변경 + git push 가능.
#   "킬스위치 해제" 단독은 모호하므로 거부.
#   "풀어줘", "해제해", "OFF" 등 다른 표현 모두 거부.
#
# 풀린 후에도 accident_send_settings.send_armed 게이트 + 사용자 클릭 확인이 추가로 필요합니다.
# 발송 1회 끝나면 즉시 MASTER_KILL_SWITCH=True 로 복귀 + push.
# ─────────────────────────────────────────────────────────────────────────
MASTER_KILL_SWITCH = False  # 2026-05-19 사용자 "사고대차 킬스위치 해제" 지시. send_armed 게이트는 여전히 작동.


def fmt_won(n) -> str:
    return f'{int(n or 0):,}원'


# ============================================================
# 1) 미입금 대상 로드
# ============================================================
def load_unpaid_contracts(sb, owner: str):
    """
    accident_rentals 테이블에서 owner='hq'|'jiip' 별 미입금 대상 조회.
    조건:
      - owner 일치
      - status = '청구완료'
      - deposit_date IS NULL
      - is_deleted != true  (휴지통 제외)
      - billing_date <= today - MIN_OVERDUE_DAYS  (청구 후 N일 이상 경과)
      - billing_date >= cutoff (해당 owner settings 의 cutoff_billing_date)
    """
    settings = sb.table('accident_send_settings').select(
        'cutoff_billing_date'
    ).eq('owner', owner).single().execute().data
    cutoff = settings.get('cutoff_billing_date') if settings else None

    today = datetime.now(KST).date()
    overdue_threshold = (today - timedelta(days=MIN_OVERDUE_DAYS)).isoformat()

    q = (sb.table('accident_rentals').select('*')
         .eq('owner', owner)
         .eq('status', '청구완료')
         .is_('deposit_date', 'null')
         .neq('is_deleted', True)
         .lte('billing_date', overdue_threshold))
    if cutoff:
        q = q.gte('billing_date', cutoff)
    return q.execute().data or []


def load_excluded_ids(sb, owner: str) -> set:
    rows = (sb.table('accident_excluded_contracts')
              .select('contract_id')
              .eq('owner', owner)
              .execute().data or [])
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
def build_message_plan(sb, owner: str, contracts: list, excluded_ids: set,
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
    ).eq('owner', owner).single().execute().data
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


def send_plan(plan: dict, *, owner: str, dry_run: bool, trigger_type: str, triggered_by: str, sb=None) -> dict:
    """
    plan 의 messages 를 실제 발송 + 로그. owner='hq'|'jiip' 도메인 별 독립.

    안전 게이트:
        1. 메시지 0건이면 즉시 종료
        2. MASTER_KILL_SWITCH=True → 강제 dry_run (코드 레벨 안전장치)
        3. 실 발송 (owner 별 발신번호) → 솔라피 호출 → 로그(owner 태그)

    참고: send_armed (1회용 무장) 게이트는 2026-05-20 사용자 결정으로 폐기.
    auto_send_enabled (자동발송 스위치 ON/OFF) 단일 게이트로 운영.
    OFF 면 auto_send.py 에서 owner 진입 자체가 차단되므로 send_plan 까지 안 옴.
    """
    if sb is None:
        sb = get_client()

    msgs = list(plan.get('messages') or [])
    if not msgs:
        return {'sent': 0, 'failed': 0, 'dry_run': dry_run, 'count': 0}

    # MASTER KILL SWITCH (코드 레벨)
    if MASTER_KILL_SWITCH and not dry_run:
        print(f'[KILL_SWITCH] MASTER_KILL_SWITCH=True → 강제 dry_run (owner={owner}, triggered_by={triggered_by})')
        dry_run = True

    if dry_run:
        return {
            'sent': 0, 'failed': 0, 'dry_run': True,
            'count': len(msgs), 'messages': msgs,
            'owner': owner,
            'blocked_by_kill_switch': MASTER_KILL_SWITCH,
        }

    # 실 발송 (owner 별 발신번호)
    from_number = from_number_for(owner).replace('-', '')
    if not from_number:
        print(f'[ERROR] owner={owner} 발신번호 미설정 (SOLAPI_FROM_{owner.upper()}) — 발송 중단')
        return {
            'sent': 0, 'failed': len(msgs), 'dry_run': True,
            'count': len(msgs), 'owner': owner,
            'blocked_by_missing_from': True,
        }
    payload_msgs = []
    for m in msgs:
        pm = {
            'to': m['phone'],
            'from': from_number,
            'text': m['text'],
            'type': m['msg_type'],
        }
        if m['msg_type'] == 'LMS':
            pm['subject'] = '[비엘렌터카] 사고대차 미입금 안내'
        payload_msgs.append(pm)

    status_code, body = _post_solapi(payload_msgs)
    print(f'[SOLAPI] owner={owner} HTTP {status_code}')

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
            'owner': owner,
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

    return {
        'sent': sent, 'failed': failed, 'owner': owner,
        'count': len(msgs), 'group_id': group_id, 'status_code': status_code,
    }
