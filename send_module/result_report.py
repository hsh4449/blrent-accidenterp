"""
자동 독촉 발송 결과(솔라피 접수 결과)를 담당자에게 문자로 보고.

호출 지점: auto_send.run_one() 이 owner 하나의 자동 독촉 처리를 끝낸 직후.
  - 정상 발송 후            → report_auto_send(sb, owner, today, plan, result)
  - 발송 대상 0건 게이트     → report_no_target(sb, owner, today)
  - run_one 예외             → report_error(sb, owner, today, exc)

원칙
  - 실제 발송 요청에 포함된 계약(plan.messages[].items_brief)만 사용. 미입금을 다시 조회하지 않음.
  - '접수 결과'만 보고. 수신 완료 여부는 다루지 않음(제목·집계·주석에 명시).
  - 판정: 솔라피가 거절을 명확히 반환한 경우만 '접수 거절', 접수 여부를 알 수 없으면 '확인 중'.
      * 네트워크 타임아웃/연결 오류/5xx/응답 해석 실패/집계 불일치 → 확인 중 (확정 실패로 단정하지 않음)
      * 4xx + errorCode/errorMessage, failedMessageList 에 수신번호 포함 → 접수 거절
  - 보고 문자 기록은 accident_result_reports (독촉 로그와 분리). 파트별 상태:
      prepared(준비: 요청 전) → sending(처리 중: 요청 시도) → registered(접수 성공) | failed(확정 거절) | unknown(확인 중)
    · 발송 전 기록 후 프로세스 종료 → prepared 로 남음 (미발송, 식별 가능)
    · 요청 후 결과 기록 실패/중단 → sending 으로 남음 (접수 여부 불명, 확인 필요, 자동 재발송 안 함)
  - 같은 (owner, run_date) 재실행 시: registered/failed/unknown/sending 파트는 다시 보내지 않고,
    prepared(요청을 시도한 적 없는) 파트만 저장된 본문으로 발송. DB 기록만으로 성공 처리하지 않음.
  - 이 모듈은 send_plan 을 절대 호출하지 않음 → 보고 실패가 독촉 재발송을 일으킬 수 없음.
  - 모든 예외는 함수 안에서 처리(print) → 다른 owner 독촉 처리를 막지 않음.
"""
import os
from datetime import datetime

import requests

from db import KST
from solapi_sender import _auth_header, byte_len, API_URL

# 보고 발신번호 (요구사항: 공통 0425245757). 환경변수로 덮어쓸 수 있음.
REPORT_FROM = os.environ.get('SOLAPI_REPORT_FROM', '0425245757')
# 솔라피 LMS 본문 한도 2,000 byte(EUC-KR 환산). 여유를 두고 분할.
LMS_MAX_BYTES = int(os.environ.get('REPORT_LMS_MAX_BYTES', '1900'))
REPORT_SUBJECT = '[비엘렌트카] 자동발송 접수 결과'
TITLE = '[비엘렌트카 자동발송 접수 결과]'
SIGNATURE = '- 비엘렌트카'
NOTE = '※ 솔라피 접수 결과이며 수신 완료 여부가 아닙니다.'
ROW_HEADER = '우리차량번호 / 고객차량번호 / 고객차종 / 보험사'
TABLE = 'accident_result_reports'

S_PREPARED, S_SENDING, S_REGISTERED, S_FAILED, S_UNKNOWN = 'prepared', 'sending', 'registered', 'failed', 'unknown'


def _now():
    return datetime.now(KST).isoformat()


def _norm_phone(p) -> str:
    return (p or '').replace('-', '').replace(' ', '').strip()


# ============================================================
# 1) 접수 결과 판정 (순수 함수)
# ============================================================
def judge_response(status_code, body, *, expect_count: int, my_phones: list) -> tuple:
    """
    솔라피 send-many/detail 응답 하나를 판정.
    반환: (mode, failed_by_phone, reason)
      mode = 'registered' | 'failed' | 'unknown'
        - 'failed'     : 솔라피가 거절을 명확히 반환 (4xx + errorCode/errorMessage)
        - 'registered' : HTTP 2xx, count.registeredSuccess/registeredFailed 가 개수와 정확히 일치
        - 'unknown'    : 그 외 전부 (None=네트워크 예외, 5xx, 해석 실패, 집계 없음/불일치)
      failed_by_phone = failedMessageList 기반 {phone: reason} (mode 와 별개로 개별 거절 반영)
    """
    body = body if isinstance(body, dict) else {}
    if status_code is None:
        return S_UNKNOWN, {}, '네트워크 오류로 접수 여부 미확인'
    if status_code >= 500:
        return S_UNKNOWN, {}, f'솔라피 서버 오류 HTTP {status_code} (접수 여부 미확인)'
    if status_code >= 400:
        if body.get('errorCode') or body.get('errorMessage'):
            return S_FAILED, {}, f'접수 거절: {str(body.get("errorMessage") or body.get("errorCode"))[:60]}'
        return S_UNKNOWN, {}, f'HTTP {status_code} 응답 해석 불가 (접수 여부 미확인)'
    if body.get('raw') is not None and not body.get('groupInfo'):
        return S_UNKNOWN, {}, '응답 해석 실패 (접수 여부 미확인)'
    failed_by_phone = {}
    for f in (body.get('failedMessageList') or []):
        if isinstance(f, dict):
            failed_by_phone[_norm_phone(f.get('to'))] = f'접수 거절: {str(f.get("statusMessage") or f.get("statusCode") or "-")[:60]}'
    count = (body.get('groupInfo') or {}).get('count') or {}
    rs, rf = count.get('registeredSuccess'), count.get('registeredFailed')
    n_failed = sum(1 for p in my_phones if _norm_phone(p) in failed_by_phone)
    n_ok = expect_count - n_failed
    if isinstance(rs, int) and isinstance(rf, int) and rs == n_ok and rf == n_failed:
        return S_REGISTERED, failed_by_phone, None
    return S_UNKNOWN, failed_by_phone, '솔라피 집계 없음/불일치 (접수 여부 미확인)'


def classify_messages(msgs: list, status_code, body) -> list:
    """독촉 send_plan 결과를 문자(수신번호) 단위로 분류.
    반환: [{'phone','status','reason','contract_ids','items_brief'}] status ∈ registered/failed/unknown"""
    mode, failed_by_phone, reason = judge_response(status_code, body, expect_count=len(msgs), my_phones=[m['phone'] for m in msgs])
    out = []
    for m in msgs:
        ph = _norm_phone(m['phone'])
        if ph in failed_by_phone:
            st, rs = S_FAILED, failed_by_phone[ph]
        elif mode == S_REGISTERED:
            st, rs = S_REGISTERED, None
        elif mode == S_FAILED:
            st, rs = S_FAILED, reason
        else:
            st, rs = S_UNKNOWN, reason
        out.append({'phone': m['phone'], 'status': st, 'reason': rs,
                    'contract_ids': m.get('contract_ids') or [], 'items_brief': m.get('items_brief') or []})
    return out


# ============================================================
# 2) 본문 빌드 + 분할 (순수 함수)
# ============================================================
def _row(item: dict) -> str:
    return ' / '.join([
        str(item.get('vehicle_number') or '-'),    # 우리 차량번호 (accident_rentals.vehicle_number)
        str(item.get('customer_number') or '-'),   # 고객 차량번호 (accident_rentals.customer_number)
        str(item.get('customer_vehicle') or '-'),  # 고객 차종     (accident_rentals.customer_vehicle)
        str(item.get('insurer') or '-'),           # 보험사       (accident_rentals.insurer)
    ])


def build_report_texts(run_date: str, classified: list, *, max_bytes: int = LMS_MAX_BYTES) -> list:
    """접수 결과 보고 본문 목록 (1개 이상). 길이 초과 시 계약 행 단위로 분할하고 제목에 (i/n) 표기."""
    reg = [c for c in classified if c['status'] == S_REGISTERED]
    fail = [c for c in classified if c['status'] == S_FAILED]
    unk = [c for c in classified if c['status'] == S_UNKNOWN]
    contracts_ok = sum(len(c['contract_ids']) for c in reg)
    summary = [f'문자 접수 성공 {len(reg)}통 / 접수 거절 {len(fail)}통 / 확인 중 {len(unk)}통',
               f'접수 성공 문자에 포함된 계약 {contracts_ok}건']

    sections = []
    ok_rows = [_row(it) for c in reg for it in c['items_brief']]
    sections.append(('[접수 성공 건]', ok_rows if ok_rows else ['없음']))
    fail_rows = []
    for c in fail:
        fail_rows += [_row(it) for it in c['items_brief']]
        fail_rows.append(f'거절 사유: {c["reason"]}')
    sections.append(('[접수 거절 건]', fail_rows if fail_rows else ['없음']))
    if unk:
        unk_rows = []
        for c in unk:
            unk_rows += [_row(it) for it in c['items_brief']]
            unk_rows.append(f'사유: {c["reason"]}')
        sections.append(('[확인 중 건]', unk_rows))

    lines = []
    for title, rows in sections:
        if lines:
            lines.append(('', False))
        lines.append((title, True))
        if rows != ['없음']:
            lines.append((ROW_HEADER, False))
        for r in rows:
            lines.append((r, False))

    def render(part_lines, i, n):
        head = TITLE + (f' ({i}/{n})' if n > 1 else '')
        body = [head, run_date, ''] + summary + ['']
        return '\n'.join(body + part_lines + ['', NOTE, SIGNATURE])

    all_lines = [l for l, _ in lines]
    if byte_len(render(all_lines, 1, 1)) <= max_bytes:
        return [render(all_lines, 1, 1)]

    parts, cur, cur_section = [], [], None
    for text, is_title in lines:
        if is_title:
            cur_section = text
        candidate = cur + [text]
        if cur and byte_len(render(candidate, 1, 99)) > max_bytes:
            parts.append(cur)
            cur = ([f'{cur_section} (계속)', ROW_HEADER] if cur_section and not is_title else []) + [text]
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    n = len(parts)
    return [render(p, i + 1, n) for i, p in enumerate(parts)]


def build_no_target_text(run_date: str) -> str:
    return '\n'.join([TITLE, run_date, '', '오늘 자동발송 대상 0건', '', SIGNATURE])


def build_error_text(run_date: str, exc: BaseException) -> str:
    return '\n'.join([TITLE, run_date, '',
                      '자동발송 처리 중 오류가 발생했습니다.',
                      f'오류: {type(exc).__name__}: {str(exc)[:60]}',
                      '독촉 문자 발송 여부는 ERP 발송 이력에서 확인이 필요합니다.', '', SIGNATURE])


# ============================================================
# 3) 상태 기록 + 발송 (owner·run_date·part 단위, 재실행 안전)
# ============================================================
def _settings(sb, owner: str) -> dict:
    return (sb.table('accident_send_settings').select('report_enabled, report_phone')
              .eq('owner', owner).single().execute().data) or {}


def _existing_parts(sb, owner: str, run_date: str) -> list:
    return (sb.table(TABLE).select('*').eq('owner', owner).eq('run_date', run_date)
              .order('part_no').execute().data) or []


def _prepare_parts(sb, owner: str, run_date: str, kind: str, to_phone: str, texts: list) -> list:
    """파트별 행을 prepared 상태로 먼저 기록. 기록 실패 시 예외 → 발송하지 않음."""
    n = len(texts)
    rows = []
    for i, text in enumerate(texts, 1):
        rows.append({'owner': owner, 'run_date': run_date, 'kind': kind, 'part_no': i, 'part_total': n,
                     'recipient_phone': to_phone, 'message_type': 'LMS' if byte_len(text) > 90 else 'SMS',
                     'message_text': text, 'status': S_PREPARED, 'status_detail': None})
    return sb.table(TABLE).insert(rows).execute().data or []


def _update(sb, row_id, patch: dict) -> bool:
    try:
        sb.table(TABLE).update({**patch, 'updated_at': _now()}).eq('id', row_id).execute()
        return True
    except Exception as e:
        print(f'[REPORT] 상태 기록 실패 id={row_id} {patch.get("status")}: {type(e).__name__}: {e}')
        return False


def _send_part(sb, row: dict) -> dict:
    """prepared 파트 1개 발송. 요청 직전 sending 으로 기록하고, 응답 판정 후 최종 상태 기록."""
    rid, text, to_phone = row['id'], row['message_text'], _norm_phone(row['recipient_phone'])
    msg_type = row.get('message_type') or ('LMS' if byte_len(text) > 90 else 'SMS')
    if not _update(sb, rid, {'status': S_SENDING, 'attempted_at': _now()}):
        # 상태를 sending 으로 못 바꾸면 추적 불가 → 이번엔 보내지 않음 (prepared 로 남아 미발송 식별)
        return {'part': row['part_no'], 'status': S_PREPARED, 'detail': '상태 기록 실패로 미발송'}
    payload = {'to': to_phone, 'from': REPORT_FROM.replace('-', ''), 'text': text, 'type': msg_type}
    if msg_type == 'LMS':
        payload['subject'] = REPORT_SUBJECT
    status_code, body = None, {}
    try:
        resp = requests.post(API_URL, json={'messages': [payload]},
                             headers={'Authorization': _auth_header(), 'Content-Type': 'application/json'}, timeout=30)
        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = {'raw': resp.text[:500]}
    except requests.RequestException as e:      # 타임아웃·연결 오류 → 확인 중 (접수 여부 미확인)
        body = {'exception': f'{type(e).__name__}: {str(e)[:200]}'}
    mode, failed_by_phone, reason = judge_response(status_code, body, expect_count=1, my_phones=[to_phone])
    if to_phone in failed_by_phone:
        mode, reason = S_FAILED, failed_by_phone[to_phone]
    group_id = (body.get('groupInfo') or {}).get('_id') or body.get('groupId')
    patch = {'status': mode, 'status_detail': reason, 'solapi_group_id': group_id, 'solapi_status_code': status_code,
             'solapi_response': body if mode != S_REGISTERED else None, 'finished_at': _now()}
    recorded = _update(sb, rid, patch)   # 실패 시 sending 으로 남음 = '접수 후 기록 실패' 식별
    print(f'[REPORT owner={row["owner"]}] {row["kind"]} part {row["part_no"]}/{row["part_total"]} HTTP {status_code} → {mode}'
          f'{"" if recorded else " (결과 기록 실패, sending 유지)"}')
    return {'part': row['part_no'], 'status': mode if recorded else S_SENDING, 'http': status_code, 'group_id': group_id, 'detail': reason}


def _guarded(sb, owner: str, today, kind: str, texts_fn) -> dict:
    run_date = today.isoformat() if hasattr(today, 'isoformat') else str(today)
    try:
        st = _settings(sb, owner)
        if not st.get('report_enabled') or not st.get('report_phone'):
            return {'owner': owner, 'skipped': 'report disabled or no phone'}
        existing = _existing_parts(sb, owner, run_date)
        if existing:
            # 재실행: 기록만으로 성공 처리하지 않음. prepared(요청 시도 전) 파트만 저장된 본문으로 발송, 나머지는 건드리지 않음.
            to_send = [r for r in existing if r.get('status') == S_PREPARED]
            untouched = {r['part_no']: r.get('status') for r in existing if r.get('status') != S_PREPARED}
            results = [_send_part(sb, r) for r in to_send]
            return {'owner': owner, 'rerun': True, 'sent_prepared_parts': [r['part'] for r in results], 'results': results,
                    'untouched_parts': untouched}
        texts = texts_fn(run_date)
        rows = _prepare_parts(sb, owner, run_date, kind, _norm_phone(st['report_phone']), texts)
        if len(rows) != len(texts):
            return {'owner': owner, 'error': f'prepared rows {len(rows)} != parts {len(texts)} (미발송)'}
        results = [_send_part(sb, r) for r in sorted(rows, key=lambda r: r['part_no'])]
        return {'owner': owner, 'parts': len(texts), 'results': results}
    except Exception as e:
        print(f'[REPORT owner={owner}] 보고 실패 (독촉 처리에는 영향 없음): {type(e).__name__}: {e}')
        return {'owner': owner, 'error': f'{type(e).__name__}: {e}'}


def report_auto_send(sb, owner: str, today, plan: dict, result: dict) -> dict:
    """자동 독촉 send_plan 직후 호출. result 에 send_plan 이 돌려준 status_code/body 가 있어야 함."""
    msgs = list(plan.get('messages') or [])
    classified = classify_messages(msgs, result.get('status_code'), result.get('body') or {})
    return _guarded(sb, owner, today, 'result', lambda rd: build_report_texts(rd, classified))


def report_no_target(sb, owner: str, today) -> dict:
    return _guarded(sb, owner, today, 'no_target', lambda rd: [build_no_target_text(rd)])


def report_error(sb, owner: str, today, exc: BaseException) -> dict:
    return _guarded(sb, owner, today, 'error', lambda rd: [build_error_text(rd, exc)])
