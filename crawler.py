"""
IMS Form 사고대차 크롤러
- 매일 오전 9시(KST) GitHub Actions에서 실행
- 지정된 차량번호별로 IMS에서 검색 → Supabase DB 업데이트
"""

import os
import re
import hashlib
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from supabase import create_client

# 환경변수
IMS_ID = os.environ['IMS_ID']
IMS_PW = os.environ['IMS_PW']
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_KEY']

# 크롤링 대상 차량번호
VEHICLE_NUMBERS = [
    '9579', '8089', '9470', '7725', '9879',
    '9894', '7950', '7940', '4926', '7034'
]

IMS_BASE = 'https://imsform.com'
API_BASE = 'https://api.rencar.co.kr'


def login(session):
    """IMS 로그인 - api.rencar.co.kr/auth → JWT 쿠키 설정"""
    # 비밀번호 SHA256 해싱
    pw_hash = hashlib.sha256(IMS_PW.encode('utf-8')).hexdigest()

    resp = session.post(
        f'{API_BASE}/auth',
        json={'username': IMS_ID, 'password': pw_hash},
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Origin': IMS_BASE,
            'Referer': f'{IMS_BASE}/',
        }
    )

    if resp.status_code != 200:
        print(f'[LOGIN] 실패: {resp.status_code} {resp.text[:200]}')
        return False

    data = resp.json()
    token = data.get('access_token') or data.get('token')
    if not token:
        # 응답 전체에서 토큰 찾기
        for key in data:
            if 'token' in key.lower() or 'jwt' in key.lower():
                token = data[key]
                break

    if not token:
        print(f'[LOGIN] 토큰 없음: {list(data.keys())}')
        return False

    # imsform.com 도메인에 JWT 쿠키 설정
    session.cookies.set('production-imsform-jwt', token, domain='.imsform.com')

    # 검증: 홈페이지 접근 테스트
    test = session.get(f'{IMS_BASE}/home')
    if test.status_code == 200 and '권한이 없습니다' not in test.text:
        print(f'[LOGIN] 성공')
        return True

    print(f'[LOGIN] 쿠키 설정 후 접근 실패, 다른 방식 시도')

    # Authorization 헤더도 설정
    session.headers['Authorization'] = f'JWT {token}'
    test2 = session.get(f'{IMS_BASE}/home')
    if test2.status_code == 200:
        print(f'[LOGIN] Authorization 헤더로 성공')
        return True

    print(f'[LOGIN] 로그인 실패')
    return False


def parse_date(text):
    """날짜 텍스트 → YYYY-MM-DD"""
    if not text or text.strip() in ('-', ''):
        return None
    text = text.strip()
    m = re.match(r'^(\d{2})\.(\d{2})\.(\d{2})$', text)
    if m:
        return f'20{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = re.match(r'^(\d{4})\.(\d{2})\.(\d{2})$', text)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', text)
    if m:
        return text
    return None


def parse_time(text):
    """시간 텍스트 파싱"""
    if not text or text.strip() in ('-', ''):
        return None
    m = re.match(r'(\d{1,2}):(\d{2})', text.strip())
    if m:
        return f'{int(m.group(1)):02d}:{m.group(2)}'
    return None


def parse_money(text):
    """금액 파싱"""
    if not text or text.strip() in ('-', ''):
        return 0
    return int(re.sub(r'[^\d]', '', text) or '0')


def search_vehicle(session, car_number):
    """차량번호로 검색하여 계약 목록 반환"""
    contracts = []
    page = 1

    while True:
        url = f'{IMS_BASE}/contract/list/all?page={page}&option=rent_car_number&value={car_number}&is_corporation=all'
        resp = session.get(url)

        if resp.status_code != 200:
            print(f'  [ERROR] 페이지 {page}: {resp.status_code}')
            break

        if '권한이 없습니다' in resp.text:
            print(f'  [ERROR] 접근 권한 없음')
            break

        soup = BeautifulSoup(resp.text, 'html.parser')
        table = soup.find('table')
        if not table:
            break

        tbody = table.find('tbody')
        if not tbody:
            break

        rows = tbody.find_all('tr')
        if not rows:
            break

        # "검색 결과가 없습니다" 체크
        first_td = rows[0].find('td')
        if first_td and '검색 결과가 없습니다' in first_td.get_text():
            break

        found_in_page = 0
        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 20:
                continue

            try:
                def cell_texts(idx):
                    texts = cells[idx].get_text(separator='\n').strip().split('\n')
                    return [t.strip() for t in texts if t.strip()]

                ct = cell_texts
                ims_id = ct(1)[0] if ct(1) else None
                if not ims_id or not ims_id.isdigit():
                    continue

                status_raw = ct(2)[0] if ct(2) else '배차중'
                status_map = {'배차중':'배차중','청구대기':'청구대기','청구검수중':'청구대기','청구완료':'청구완료','계약종결':'계약종결'}
                status = status_map.get(status_raw, status_raw)

                start_p = ct(4)
                end_p = ct(5)
                bill_p = ct(6)
                dep_p = ct(7)
                veh_p = ct(8)
                cust_p = ct(10)

                contract = {
                    'id': ims_id,
                    'status': status,
                    'dispatcher': ct(3)[0] if ct(3) else '-',
                    'start_date': parse_date(start_p[0]) if start_p else None,
                    'start_time': parse_time(start_p[1]) if len(start_p) > 1 else None,
                    'end_date': parse_date(end_p[0]) if end_p else None,
                    'end_time': parse_time(end_p[1]) if len(end_p) > 1 else None,
                    'billing_date': parse_date(bill_p[0]) if bill_p else None,
                    'billing_time': parse_time(bill_p[1]) if len(bill_p) > 1 else None,
                    'deposit_date': parse_date(dep_p[0]) if dep_p else None,
                    'vehicle_model': veh_p[0] if veh_p else '',
                    'vehicle_number': veh_p[1] if len(veh_p) > 1 else '',
                    'customer_name': ct(9)[0] if ct(9) else '',
                    'customer_vehicle': cust_p[0] if cust_p else '',
                    'customer_number': cust_p[1] if len(cust_p) > 1 else '',
                    'customer_phone': ct(11)[0] if ct(11) else '',
                    'fault': ct(12)[0] if ct(12) else '-',
                    'insurer': ct(13)[0] if ct(13) else '-',
                    'billing_to': ct(14)[0] if ct(14) else '-',
                    'receipt_no': ct(15)[0] if ct(15) else '',
                    'sales_rep': ct(16)[0] if ct(16) else '-',
                    'retriever': ct(17)[0] if ct(17) else '-',
                    'referrer': ct(18)[0] if ct(18) else '-',
                    'repair_shop': ct(19)[0] if ct(19) else '-',
                    'billing_amount': parse_money(ct(20)[0]) if ct(20) else 0,
                    'rental_fee': parse_money(ct(21)[0]) if ct(21) else 0,
                }
                contracts.append(contract)
                found_in_page += 1

            except Exception as e:
                print(f'  [WARN] 파싱 오류: {e}')
                continue

        print(f'  page {page}: {found_in_page}건')

        if found_in_page < 20:
            break
        page += 1

    return contracts


def main():
    print(f'=== IMS 사고대차 크롤링 시작: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')

    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    })

    if not login(session):
        print('[FATAL] 로그인 실패, 종료')
        return

    all_contracts = []
    for car_num in VEHICLE_NUMBERS:
        print(f'\n[SEARCH] {car_num}')
        results = search_vehicle(session, car_num)
        print(f'  → {len(results)}건')
        all_contracts.extend(results)

    # 중복 제거
    seen = {}
    unique = []
    for c in all_contracts:
        if c['id'] not in seen:
            seen[c['id']] = True
            unique.append(c)

    print(f'\n[TOTAL] {len(unique)}건 (중복 제거)')

    if unique:
        result = supabase_client.table('accident_rentals').upsert(unique, on_conflict='id').execute()
        print(f'[DB] Supabase {len(unique)}건 upsert 완료')
    else:
        print('[DB] 업데이트할 데이터 없음')

    print(f'\n=== 완료: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')


if __name__ == '__main__':
    main()
