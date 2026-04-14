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
from datetime import datetime
from supabase import create_client

IMS_ID = os.environ['IMS_ID']
IMS_PW = os.environ['IMS_PW']
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

VEHICLE_NUMBERS = [
    '9579', '8089', '9470', '7725', '9879',
    '9894', '7950', '7940', '4926', '7034'
]

STATUS_MAP = {
    'dispatch': '배차중',
    'waiting_claim': '청구대기',
    'checking_claim': '청구대기',
    'send_claim': '청구완료',
    'done': '계약종결',
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


def convert_claim(c):
    """IMS claim → DB row"""
    start_date, start_time = parse_datetime(c.get('delivered_at'))
    end_date, end_time = parse_datetime(c.get('return_date'))
    billing_date, billing_time = parse_datetime(c.get('claim_at'))

    # 입금일: claim_done_at
    deposit_date, _ = parse_datetime(c.get('claim_done_at'))

    # 상태 매핑
    status = STATUS_MAP.get(c.get('claim_state', ''), c.get('claim_state', '배차중'))

    return {
        'id': str(c.get('id', '')),
        'status': status,
        'dispatcher': c.get('rent_manager_name') or '-',
        'start_date': start_date,
        'start_time': start_time,
        'end_date': end_date,
        'end_time': end_time,
        'billing_date': billing_date,
        'billing_time': billing_time,
        'deposit_date': deposit_date,
        'vehicle_model': c.get('car_model') or '',
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
    }


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

        for c in claims:
            contracts.append(convert_claim(c))

        print(f'  page {page}/{total_pages}: {len(claims)}건')

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
    for car_num in VEHICLE_NUMBERS:
        print(f'[SEARCH] {car_num}')
        results = search_vehicle(session, car_num)
        print(f'  → {len(results)}건')
        all_contracts.extend(results)

    # 중복 제거 (같은 ID면 최신 것으로 덮어쓰기)
    seen = {}
    for c in all_contracts:
        seen[c['id']] = c  # 나중에 나온 것이 덮어씀
    unique = list(seen.values())

    print(f'\n[TOTAL] {len(unique)}건 (중복 제거)')

    if unique:
        supabase_client.table('accident_rentals').upsert(
            unique, on_conflict='id'
        ).execute()
        print(f'[DB] Supabase {len(unique)}건 upsert 완료')
    else:
        print('[DB] 업데이트할 데이터 없음')

    print(f'=== 완료: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')


if __name__ == '__main__':
    main()
