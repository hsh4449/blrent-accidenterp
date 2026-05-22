"""
IMS Form 사고대차 크롤러
- 매일 오전 9시(KST) GitHub Actions에서 실행
- __NEXT_DATA__에서 JSON 데이터 직접 추출
- 중복 시 최신 데이터로 업데이트 (upsert)
"""

import os
import re
import json
import hashlib
import requests
from datetime import datetime, timezone
from supabase import create_client

IMS_ID = os.environ['IMS_ID']
IMS_PW = os.environ['IMS_PW']
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

# 본사 사고대차 차량 (owner='hq' 로 태그)
VEHICLE_NUMBERS = [
    '9579', '8089', '9470', '7725', '9879',
    '9894', '7950', '7940', '4926', '7034',
    '3910', '5080', '6078', '7986',
]

# 신동석부장 지입차 17대 (owner='jiip' 로 태그)
# - 사고대차 ERP 사용자 모드 (코드 0000) 에서만 노출됨
# - 청구일 < JIIP_BILLING_CUTOFF 인 건은 upsert 제외 (이전 이력 무시 정책)
JIIP_VEHICLES = [
    '4286', '8993', '9194', '9256', '9334',
    '9340', '9341', '9388', '9433', '9558',
    '8433', '9666', '9759', '9755', '9754',
    '9878', '9893',
]
JIIP_BILLING_CUTOFF = '2025-12-20'  # 이전 청구는 수집 안 함

# 끝 4자리 → owner 매핑 (한 행 단위로 owner 태그 결정).
SUFFIX_TO_OWNER = (
    {s: 'hq'   for s in VEHICLE_NUMBERS}
    | {s: 'jiip' for s in JIIP_VEHICLES}
)
ALL_SUFFIXES = list(SUFFIX_TO_OWNER.keys())

# IMS 응답의 차종명이 실제와 다른 경우 차량번호 끝번호 → 모델명 강제 매핑
MODEL_OVERRIDE = {
    '7940': 'GLE 쿠페',
}


def owner_for_vehicle_number(rent_car_number):
    """차량번호 (예: '106호9433') 끝 4자리로 owner 결정. 매칭 안되면 None."""
    for sfx, ow in SUFFIX_TO_OWNER.items():
        if rent_car_number.endswith(sfx):
            return ow
    return None

STATUS_MAP = {
    'dispatch': '배차중',
    'using_car': '배차중',
    'before_claim': '청구전',
    'waiting_claim': '청구전',
    'checking_claim': '청구전',
    'send_claim': '청구완료',
    'done_claim': '입금완료',
    'done': '입금완료',
}


def login():
    """IMS 로그인 → JWT 토큰이 설정된 session 반환"""
    pw_hash = hashlib.sha256(IMS_PW.encode('utf-8')).hexdigest()
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    resp = session.post(
        'https://api.rencar.co.kr/auth',
        json={'username': IMS_ID, 'password': pw_hash},
        headers={'Content-Type': 'application/json', 'Origin': 'https://imsform.com'}
    )

    if resp.status_code != 200:
        print(f'[LOGIN] 실패: {resp.status_code}')
        return None

    token = resp.json().get('access_token')
    if not token:
        print('[LOGIN] 토큰 없음')
        return None

    session.cookies.set('production-imsform-jwt', token, domain='imsform.com')
    print('[LOGIN] 성공')
    return session


def parse_datetime(dt_str):
    """'2026-03-27 11:03:28' → ('2026-03-27', '11:03')"""
    if not dt_str:
        return None, None
    m = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', dt_str)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'(\d{4}-\d{2}-\d{2})', dt_str)
    if m:
        return m.group(1), None
    return None, None


def parse_phone(phone):
    """전화번호 포맷"""
    if not phone:
        return ''
    digits = re.sub(r'[^\d]', '', phone)
    if len(digits) == 11:
        return f'{digits[:3]}-{digits[3:7]}-{digits[7:]}'
    return phone


def make_replacement_note(c, our_numbers):
    """교체건이면 우리차 사용일수 메모 생성"""
    if not c.get('car_replaced'):
        return None
    details = c.get('details', [])
    if len(details) <= 1:
        return None

    # 전체 기간
    start_dt, _ = parse_datetime(c.get('delivered_at'))
    end_dt, _ = parse_datetime(c.get('return_date'))
    if start_dt and end_dt:
        from datetime import datetime as dt
        total_days = (dt.strptime(end_dt, '%Y-%m-%d') - dt.strptime(start_dt, '%Y-%m-%d')).days
    else:
        total_days = 0
        for d in details:
            info = d.get('claim_date_info') or {}
            total_days = info.get('total_day', 0)
            break

    # 우리차 부분만 추출
    our_parts = []
    for d in details:
        num = d.get('rent_car_number', '')
        # 우리 차량번호 끝 4자리 매칭
        is_ours = any(num.endswith(n) for n in our_numbers)
        if is_ours:
            # return_date에서 이전 detail의 return_date 빼서 일수 계산
            info = d.get('claim_date_info') or {}
            our_parts.append(num)

    if not our_parts:
        return None

    # 간단히: 우리차 번호들 / 전체일수
    parts_str = ', '.join(our_parts)
    return f'{parts_str} / 전체{total_days}일'


def convert_claim(c, our_numbers):
    """IMS claim → DB row. 메인 차량이 우리 차량 아니면 None 반환 (스킵)

    owner 컬럼:
      - rent_car_number 끝 4자리 → SUFFIX_TO_OWNER 매핑으로 'hq' / 'jiip' 결정
    JIIP cutoff:
      - owner='jiip' 이고 billing_date < JIIP_BILLING_CUTOFF 인 건은 None 반환
        (이전 청구 이력은 ERP에 가져오지 않음)
    """
    rent_car = c.get('rent_car_number') or ''
    if not any(rent_car.endswith(n) for n in our_numbers):
        return None
    row_owner = owner_for_vehicle_number(rent_car) or 'hq'

    car_model = c.get('car_model') or ''
    for suffix, override in MODEL_OVERRIDE.items():
        if rent_car.endswith(suffix):
            car_model = override
            break

    # 교체건이면 details에서 메인 차량(=ERP에 저장될 vehicle_number)과 매칭되는 detail의 기간을 사용
    # 첫 우리차 detail이 아니라 메인 차량 detail의 delivered_date/return_date를 써야 정확
    # (메인 차량이 마지막에 사용된 차이므로 그 detail이 ERP 행의 진짜 사용 기간)
    is_replaced = c.get('car_replaced', 0) == 1
    our_detail_delivered = None
    our_detail_returned = None
    if is_replaced:
        for d in [d for d in (c.get('details') or []) if d]:
            num = d.get('rent_car_number') or ''
            if num == rent_car:  # 메인 차량과 정확히 일치하는 detail
                our_detail_delivered = d.get('delivered_date')
                our_detail_returned = d.get('return_date')
                break
        # 메인과 매칭되는 detail 못 찾으면 fallback으로 우리차 매칭 detail
        if not our_detail_delivered:
            for d in [d for d in (c.get('details') or []) if d]:
                num = d.get('rent_car_number') or ''
                if num and any(num.endswith(n) for n in our_numbers):
                    our_detail_delivered = d.get('delivered_date')
                    our_detail_returned = d.get('return_date')
                    break

    raw_start = our_detail_delivered or c.get('delivered_at')
    raw_end = our_detail_returned or c.get('return_date')
    start_date, start_time = parse_datetime(raw_start)
    end_date, end_time = parse_datetime(raw_end)
    billing_date, billing_time = parse_datetime(c.get('claim_at'))

    # 지입차 cutoff: 청구일 < JIIP_BILLING_CUTOFF 이면 수집 안 함.
    # billing_date 가 비어있는 (=청구전) 건은 지입차도 일단 수집.
    if row_owner == 'jiip' and billing_date and billing_date < JIIP_BILLING_CUTOFF:
        return None

    # 입금일: claim_done_at
    deposit_date, _ = parse_datetime(c.get('claim_done_at'))

    # 상태 매핑
    # 우선 IMS claim_state 기준으로 매핑하고, deposit_date 가 있으면 입금완료로 강제.
    # 교체건은 '입금완료' 가 아닌 경우에만 '교체' 라벨 (입금완료된 교체건은 입금완료 우선).
    status = STATUS_MAP.get(c.get('claim_state', ''), c.get('claim_state', '배차중'))
    if deposit_date and status in ('청구완료', '청구전', '배차중'):
        status = '입금완료'  # IMS claim_done_at 있으면 실제 입금된 것
    if is_replaced and status != '입금완료':
        status = '교체'  # 입금 안 된 교체건만 '교체' 라벨

    # 교체건 메모
    replacement_note = None
    if is_replaced:
        details = [d for d in (c.get('details') or []) if d]
        if len(details) > 1:
            parts = []
            for d in details:
                num = d.get('rent_car_number', '')
                is_ours = any(num.endswith(n) for n in our_numbers)
                if is_ours:
                    # 일수 계산: return_date 기반
                    parts.append(num)
            # 전체 일수
            if start_date and end_date:
                from datetime import datetime as dt
                total = (dt.strptime(end_date, '%Y-%m-%d') - dt.strptime(start_date, '%Y-%m-%d')).days
            else:
                total = (details[0].get('claim_date_info') or {}).get('total_day', 0)
            if parts:
                replacement_note = f'{", ".join(parts)} / 전체{total}일'
            else:
                replacement_note = f'우리차 없음 / 전체{total}일'

    # 보험사 담당자 자동 매핑 (IMS 응답에서 추출)
    # IMS에 값이 있을 때만 채움, 비어있으면 dict에서 제거하여 ERP 사용자 입력 보존
    ims_manager_name = c.get('claim_insurance_manager') or ''
    ims_manager_phone = parse_phone(c.get('claim_insurance_contact'))

    # 교체건 외부 차량 정보 추출 (B)
    # details 배열에서 우리 차량이 아닌 첫 차량 정보를 별도 컬럼에 저장
    other_vehicle = None
    other_days = None
    other_cost = None
    other_model = None
    other_start_date = None
    other_end_date = None
    if is_replaced:
        for d in [d for d in (c.get('details') or []) if d]:
            num = d.get('rent_car_number') or ''
            if num and not any(num.endswith(n) for n in our_numbers):
                other_vehicle = num
                other_days = (d.get('cost_data') or {}).get('total_day')
                cost_str = d.get('claim_cost')
                try:
                    other_cost = int(cost_str) if cost_str else None
                except (ValueError, TypeError):
                    other_cost = None
                # 모델은 rent_car_name "차량번호 모델명" 형식에서 추출
                rent_car_name = d.get('rent_car_name') or ''
                other_model = rent_car_name.replace(num, '').strip() or None
                # 외부차 사용 기간
                other_start_date, _ = parse_datetime(d.get('delivered_date'))
                other_end_date, _ = parse_datetime(d.get('return_date'))
                break  # 외부차는 보통 1대, 첫 매칭만 사용

    row = {
        'id': str(c.get('id', '')),
        'owner': row_owner,
        'status': status,
        'dispatcher': c.get('rent_manager_name') or '-',
        'start_date': start_date,
        'start_time': start_time,
        'end_date': end_date,
        'end_time': end_time,
        'billing_date': billing_date,
        'billing_time': billing_time,
        'deposit_date': deposit_date,
        'vehicle_model': car_model,
        'vehicle_number': c.get('rent_car_number') or '',
        'customer_name': c.get('customer_name') or '',
        'customer_vehicle': c.get('customer_car') or '',
        'customer_number': c.get('customer_car_number') or '',
        'customer_phone': parse_phone(c.get('customer_contact')),
        'fault': c.get('fault_rate') or '-',
        'insurer': c.get('insurance_company') or '-',
        'billing_to': c.get('claimee_name') or '-',
        'receipt_no': c.get('registration_id') or '',
        'sales_rep': c.get('sales_employee_name') or '-',
        'retriever': c.get('retrieve_employee_name') or '-',
        'referrer': c.get('recommender_name') or '-',
        'repair_shop': c.get('industrial_company') or '-',
        'billing_amount': c.get('claim_total_cost') or 0,
        'rental_fee': c.get('deposit_cost') or 0,
        'replacement_note': replacement_note,
        'insurance_manager_name': ims_manager_name,
        'insurance_manager_phone': ims_manager_phone,
        'replacement_other_vehicle': other_vehicle,
        'replacement_other_days': other_days,
        'replacement_other_cost': other_cost,
        'replacement_other_model': other_model,
        'replacement_other_start_date': other_start_date,
        'replacement_other_end_date': other_end_date,
    }

    # IMS에 비어있는 보호 키는 dict에서 제거 → 사용자가 ERP에서 입력한 기존 값 보존
    for protect_key in ['insurance_manager_name', 'insurance_manager_phone']:
        if not row.get(protect_key):
            row.pop(protect_key, None)

    return row


def build_extra_rows(c, our_numbers, main_row):
    """교체건에 부 우리차 detail이 있으면 별도 행으로 분리 등록.
    한 IMS 계약에 두 우리차가 모두 사용된 경우, 메인 차량 외 부 우리차도 별도 행으로
    ERP에 등록해 스케줄/차량별 카드에 각자 표시되도록 한다.
    """
    if c.get('car_replaced', 0) != 1:
        return []
    main_rent_car = c.get('rent_car_number') or ''
    extras = []
    for d in [d for d in (c.get('details') or []) if d]:
        num = d.get('rent_car_number') or ''
        if not num or num == main_rent_car:
            continue
        if not any(num.endswith(n) for n in our_numbers):
            continue  # 외부차는 분리 등록 안 함 (별도 컬럼에 이미 저장)

        d_start, d_start_time = parse_datetime(d.get('delivered_date'))
        d_end, d_end_time = parse_datetime(d.get('return_date'))

        rent_car_name = d.get('rent_car_name') or ''
        d_model = rent_car_name.replace(num, '').strip() or main_row.get('vehicle_model') or ''
        for suffix, override in MODEL_OVERRIDE.items():
            if num.endswith(suffix):
                d_model = override
                break

        cost_str = d.get('claim_cost')
        try:
            d_cost = int(cost_str) if cost_str else 0
        except (ValueError, TypeError):
            d_cost = 0

        detail_id = d.get('id')
        d_id = str(detail_id) if detail_id else f"{c.get('id')}-{num}"

        new_row = {**main_row}
        new_row['id'] = d_id
        new_row['vehicle_number'] = num
        new_row['vehicle_model'] = d_model
        new_row['start_date'] = d_start
        new_row['start_time'] = d_start_time
        new_row['end_date'] = d_end
        new_row['end_time'] = d_end_time
        new_row['billing_amount'] = d_cost  # 이 detail 분담 청구금
        # 입금/대여료는 메인 행에서만 카운트 (중복 방지)
        new_row['rental_fee'] = 0
        new_row['deposit_date'] = None
        # 외부차 정보는 부 행에 의미 없음
        new_row['replacement_other_vehicle'] = None
        new_row['replacement_other_model'] = None
        new_row['replacement_other_start_date'] = None
        new_row['replacement_other_end_date'] = None
        new_row['replacement_other_days'] = None
        new_row['replacement_other_cost'] = None

        # 빈 값 제거 (보호 키)
        for k in ['insurance_manager_name', 'insurance_manager_phone']:
            if not new_row.get(k):
                new_row.pop(k, None)

        extras.append(new_row)
    return extras


def search_vehicle(session, car_number):
    """차량번호 검색 → __NEXT_DATA__에서 JSON 추출"""
    contracts = []
    page = 1

    while True:
        url = f'https://imsform.com/contract/list/all?page={page}&option=rent_car_number&value={car_number}&is_corporation=all'
        resp = session.get(url)

        if resp.status_code != 200:
            print(f'  [ERROR] page {page}: {resp.status_code}')
            break

        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text)
        if not m:
            print(f'  [ERROR] __NEXT_DATA__ 없음')
            break

        data = json.loads(m.group(1))
        api_result = data.get('props', {}).get('pageProps', {}).get('apiResult', {})
        claims = api_result.get('claimList', [])
        total_pages = api_result.get('totalPage', 1)

        skipped_other = 0
        for c in claims:
            row = convert_claim(c, ALL_SUFFIXES)
            if row is None:
                skipped_other += 1
                continue
            contracts.append(row)
            # 두 우리차 교체 케이스: 부 우리차도 별도 행으로 분리
            for extra in build_extra_rows(c, ALL_SUFFIXES, row):
                contracts.append(extra)

        msg = f'  page {page}/{total_pages}: {len(claims)}건'
        if skipped_other:
            msg += f' (우리차 아님 {skipped_other}건 제외)'
        print(msg)

        if page >= total_pages:
            break
        page += 1

    return contracts


def main():
    print(f'=== IMS 크롤링 시작: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')

    session = login()
    if not session:
        print('[FATAL] 로그인 실패')
        return

    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    all_contracts = []
    print(f'[SCOPE] 본사 {len(VEHICLE_NUMBERS)}대 + 지입 {len(JIIP_VEHICLES)}대 = {len(ALL_SUFFIXES)}대 검색')
    print(f'[SCOPE] 지입 cutoff: 청구일 >= {JIIP_BILLING_CUTOFF}')
    for car_num in ALL_SUFFIXES:
        owner_tag = SUFFIX_TO_OWNER.get(car_num, '?')
        print(f'[SEARCH] {car_num} ({owner_tag})')
        results = search_vehicle(session, car_num)
        print(f'  → {len(results)}건')
        all_contracts.extend(results)

    # 중복 제거 (같은 ID면 최신 것으로 덮어쓰기)
    seen = {}
    for c in all_contracts:
        seen[c['id']] = c
    unique = list(seen.values())
    print(f'\n[TOTAL] {len(unique)}건 (중복 제거)')

    # DB에서 이미 입금완료 + deposit_date + rental_fee>0 있는 건 제외 (재크롤 방지)
    # 단, rental_fee=0 인 입금완료 건은 = 사용자가 독촉문자 차단 위해 강제로 입금완료로 잡은 케이스
    # → 실제 입금금액(IMS deposit_cost)으로 rental_fee 채워야 하므로 매 크롤 재시도.
    settled = supabase_client.table('accident_rentals').select('id, rental_fee').eq('status', '입금완료').not_.is_('deposit_date', 'null').execute()
    settled_ids = {r['id'] for r in (settled.data or []) if (r.get('rental_fee') or 0) > 0}
    before = len(unique)
    unique = [c for c in unique if c['id'] not in settled_ids]
    skipped = before - len(unique)
    if skipped:
        print(f'[SKIP] 입금완료(rental_fee>0) {skipped}건 제외')
    # rental_fee=0 인 입금완료 건은 재시도 대상으로 포함됨
    retry_zero = sum(1 for c in unique if any(s.get('id')==c['id'] and (s.get('rental_fee') or 0)==0 for s in (settled.data or [])))
    if retry_zero:
        print(f'[RETRY] rental_fee=0 인 입금완료 {retry_zero}건 — IMS 값으로 갱신 시도')

    # 같은 차량 배차중 중복 정리 — start_date 가장 최근만 유지, 나머지 청구전으로
    # (한 차에 두 명 동시 대여 불가 → IMS의 cleanup 누락 데이터 보정)
    by_vn_active = {}
    for row in unique:
        if row.get('status') == '배차중' and row.get('vehicle_number'):
            by_vn_active.setdefault(row['vehicle_number'], []).append(row)
    for vn, items in by_vn_active.items():
        if len(items) > 1:
            items.sort(key=lambda r: r.get('start_date') or '', reverse=True)
            for r in items[1:]:
                r['status'] = '청구전'
            print(f'[FIX] {vn}: 배차중 {len(items)}건 → 최근 1건만 유지, {len(items)-1}건 청구전으로')

    # rental_fee 이상치 보호 — IMS deposit_cost는 취소된 입금 항목까지 raw 합산해서 보내는 케이스가 있음
    # (예: 대여료 776,620 입력 후 취소 → IMS UI는 776,620이지만 API deposit_cost는 1,553,240)
    # 청구금 > 0 인데 대여료 > 청구금이면 비정상 — upsert에서 rental_fee 컬럼 제외해 DB 기존 값 보존
    fee_protected = 0
    for row in unique:
        bil = row.get('billing_amount') or 0
        ren = row.get('rental_fee') or 0
        if bil > 0 and ren > bil:
            print(f'[WARN] {row["id"]} {row.get("customer_name","")}: rental_fee {ren:,} > billing_amount {bil:,} — rental_fee 보호(upsert 제외, ERP 수정값 유지)')
            row.pop('rental_fee', None)
            fee_protected += 1
    if fee_protected:
        print(f'[PROTECT] rental_fee 이상치 {fee_protected}건 — 청구금 < 대여료 케이스 보호됨')

    print(f'[UPDATE] {len(unique)}건 업데이트 대상')

    # 디버그: IMS 응답 중 DB 에 없는 신규 id 식별 (왜 신규 INSERT 가 안 일어나는지 추적)
    try:
        existing = supabase_client.table('accident_rentals').select('id').execute()
        db_ids = {r['id'] for r in (existing.data or [])}
        ims_ids = {c['id'] for c in unique}
        new_ids = ims_ids - db_ids
        print(f'[DEBUG] IMS 응답 id {len(ims_ids)}건 / DB 전체 id {len(db_ids)}건 / 차집합(신규 후보) {len(new_ids)}건')
        if new_ids:
            print(f'[DEBUG] 신규 id 샘플: {sorted(list(new_ids))[:10]}')
        # 입금완료 skip 전 raw 응답 통계
        all_ims_ids = {c['id'] for c in all_contracts}
        raw_new = all_ims_ids - db_ids
        print(f'[DEBUG] IMS raw 응답 (skip 전) id {len(all_ims_ids)}건 / 그 중 DB 에 없는 id {len(raw_new)}건')
        if raw_new:
            print(f'[DEBUG] raw 신규 id 샘플: {sorted(list(raw_new))[:10]}')
    except Exception as e:
        print(f'[DEBUG] 신규 id 추적 실패: {e}')

    # updated_at 명시적 갱신 — Supabase upsert 는 payload 에 있는 컬럼만 update.
    # payload 에 updated_at 가 없으면 row 가 실제로 변경되어도 timestamp 가 영원히 안 움직임.
    # → 매 upsert 마다 utcnow() ISO 로 동봉.
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in unique:
        row['updated_at'] = now_iso

    if unique:
        supabase_client.table('accident_rentals').upsert(
            unique, on_conflict='id'
        ).execute()
        print(f'[DB] Supabase {len(unique)}건 upsert 완료 (updated_at={now_iso})')

    # 추가 DB 정리: IMS가 옛 배차중 행을 더 이상 안 보내는 경우도 정정
    # (DB에는 남아있지만 새 배차중이 있으면 옛 행은 청구전으로)
    try:
        active_rows = supabase_client.table('accident_rentals') \
            .select('id, vehicle_number, start_date') \
            .eq('status', '배차중').eq('is_deleted', False).execute()
        by_vn_db = {}
        for r in active_rows.data or []:
            by_vn_db.setdefault(r.get('vehicle_number'), []).append(r)
        fix_ids = []
        for vn, rows in by_vn_db.items():
            if vn and len(rows) > 1:
                rows.sort(key=lambda r: r.get('start_date') or '', reverse=True)
                for r in rows[1:]:
                    fix_ids.append(r['id'])
        if fix_ids:
            supabase_client.table('accident_rentals').update({
                'status': '청구전',
                'updated_at': now_iso,
            }).in_('id', fix_ids).execute()
            print(f'[DB FIX] 배차중 중복 {len(fix_ids)}건 → 청구전 정정')
    except Exception as e:
        print(f'[WARN] 배차중 중복 DB 정리 실패: {e}')

    print(f'=== 완료: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')


if __name__ == '__main__':
    main()
