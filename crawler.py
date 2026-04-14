"""
IMS Form 사고대차 크롤러
- 매일 오전 9시(KST) GitHub Actions에서 실행
- 지정된 차량번호별로 IMS에서 검색 → Supabase DB 업데이트
"""

import os
import re
import json
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

BASE_URL = 'https://imsform.com'


def login(session):
    """IMS 로그인"""
    # 로그인 페이지 접속하여 토큰 확인
    resp = session.get(f'{BASE_URL}/')

    # 로그인 요청
    login_data = {
        'login_id': IMS_ID,
        'password': IMS_PW,
    }

    # IMS form은 SPA이므로 API 엔드포인트로 로그인 시도
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Referer': f'{BASE_URL}/',
        'Origin': BASE_URL,
    }

    # 먼저 일반 폼 로그인 시도
    resp = session.post(f'{BASE_URL}/api/auth/login',
                        json=login_data, headers=headers)

    if resp.status_code == 200:
        print(f'[LOGIN] 로그인 성공')
        return True

    # 다른 엔드포인트 시도
    resp = session.post(f'{BASE_URL}/login',
                        data={'login_id': IMS_ID, 'password': IMS_PW},
                        allow_redirects=True)

    if 'home' in resp.url or resp.status_code == 200:
        print(f'[LOGIN] 로그인 성공 (폼)')
        return True

    print(f'[LOGIN] 로그인 실패: {resp.status_code}')
    return False


def parse_date(text):
    """날짜 텍스트 파싱 → YYYY-MM-DD"""
    if not text or text.strip() in ('-', ''):
        return None
    text = text.strip()

    # 26.03.16 형식
    m = re.match(r'^(\d{2})\.(\d{2})\.(\d{2})$', text)
    if m:
        yy, mm, dd = m.groups()
        year = 2000 + int(yy)
        return f'{year}-{mm}-{dd}'

    # 2026.03.16 형식
    m = re.match(r'^(\d{4})\.(\d{2})\.(\d{2})$', text)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'

    # 2026-03-16 형식
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', text)
    if m:
        return text

    return None


def parse_time(text):
    """시간 텍스트 파싱"""
    if not text or text.strip() in ('-', ''):
        return None
    text = text.strip()
    m = re.match(r'(\d{1,2}):(\d{2})', text)
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
        url = f'{BASE_URL}/contract/list/all?page={page}&option=rent_car_number&value={car_number}&is_corporation=all'
        resp = session.get(url)

        if resp.status_code != 200:
            print(f'  [ERROR] 페이지 {page} 요청 실패: {resp.status_code}')
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
        first_cell = rows[0].find('td')
        if first_cell and '검색 결과가 없습니다' in first_cell.get_text():
            break

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 22:
                continue

            # 각 셀에서 텍스트 추출
            def cell_text(idx):
                cell = cells[idx]
                texts = cell.get_text(separator='\n').strip().split('\n')
                return [t.strip() for t in texts if t.strip()]

            try:
                ims_id = cell_text(1)[0] if cell_text(1) else None
                if not ims_id or not ims_id.isdigit():
                    continue

                status = cell_text(2)[0] if cell_text(2) else '배차중'
                dispatcher = cell_text(3)[0] if cell_text(3) else '-'

                # 배차일 (날짜 + 시간)
                start_parts = cell_text(4)
                start_date = parse_date(start_parts[0]) if start_parts else None
                start_time = parse_time(start_parts[1]) if len(start_parts) > 1 else None

                # 반납일
                end_parts = cell_text(5)
                end_date = parse_date(end_parts[0]) if end_parts else None
                end_time = parse_time(end_parts[1]) if len(end_parts) > 1 else None

                # 청구일
                bill_parts = cell_text(6)
                billing_date = parse_date(bill_parts[0]) if bill_parts else None
                billing_time = parse_time(bill_parts[1]) if len(bill_parts) > 1 else None

                # 입금일
                deposit_parts = cell_text(7)
                deposit_date = parse_date(deposit_parts[0]) if deposit_parts else None

                # 대여차종/차량번호
                vehicle_parts = cell_text(8)
                vehicle_model = vehicle_parts[0] if vehicle_parts else ''
                vehicle_number = vehicle_parts[1] if len(vehicle_parts) > 1 else ''

                # 고객명
                customer_name = cell_text(9)[0] if cell_text(9) else ''

                # 고객차/차량번호
                cust_parts = cell_text(10)
                customer_vehicle = cust_parts[0] if cust_parts else ''
                customer_number = cust_parts[1] if len(cust_parts) > 1 else ''

                # 나머지 필드
                customer_phone = cell_text(11)[0] if cell_text(11) else ''
                fault = cell_text(12)[0] if cell_text(12) else '-'
                insurer = cell_text(13)[0] if cell_text(13) else '-'
                billing_to = cell_text(14)[0] if cell_text(14) else '-'
                receipt_no = cell_text(15)[0] if cell_text(15) else ''
                sales_rep = cell_text(16)[0] if cell_text(16) else '-'
                retriever = cell_text(17)[0] if cell_text(17) else '-'
                referrer = cell_text(18)[0] if cell_text(18) else '-'
                repair_shop = cell_text(19)[0] if cell_text(19) else '-'
                billing_amount = parse_money(cell_text(20)[0]) if cell_text(20) else 0
                rental_fee = parse_money(cell_text(21)[0]) if cell_text(21) else 0

                # 상태 매핑
                status_map = {
                    '배차중': '배차중',
                    '청구대기': '청구대기',
                    '청구검수중': '청구대기',
                    '청구완료': '청구완료',
                    '계약종결': '계약종결',
                }
                status = status_map.get(status, status)

                contract = {
                    'id': ims_id,
                    'status': status,
                    'dispatcher': dispatcher,
                    'start_date': start_date,
                    'start_time': start_time,
                    'end_date': end_date,
                    'end_time': end_time,
                    'billing_date': billing_date,
                    'billing_time': billing_time,
                    'deposit_date': deposit_date,
                    'vehicle_model': vehicle_model,
                    'vehicle_number': vehicle_number,
                    'customer_name': customer_name,
                    'customer_vehicle': customer_vehicle,
                    'customer_number': customer_number,
                    'customer_phone': customer_phone,
                    'fault': fault,
                    'insurer': insurer,
                    'billing_to': billing_to,
                    'receipt_no': receipt_no,
                    'sales_rep': sales_rep,
                    'retriever': retriever,
                    'referrer': referrer,
                    'repair_shop': repair_shop,
                    'billing_amount': billing_amount,
                    'rental_fee': rental_fee,
                }
                contracts.append(contract)

            except Exception as e:
                print(f'  [WARN] 행 파싱 오류: {e}')
                continue

        # 다음 페이지 확인
        pagination = soup.find_all('div', recursive=True)
        has_next = False
        for div in pagination:
            # 다음 페이지 버튼 찾기
            page_links = div.find_all(string=str(page + 1))
            if page_links:
                has_next = True
                break

        if not has_next:
            break

        page += 1

    return contracts


def upsert_to_supabase(supabase_client, contracts):
    """Supabase에 upsert"""
    if not contracts:
        return 0

    # 배치로 upsert
    result = supabase_client.table('accident_rentals').upsert(
        contracts, on_conflict='id'
    ).execute()

    return len(contracts)


def main():
    print(f'=== IMS 사고대차 크롤링 시작: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')

    # Supabase 클라이언트
    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # IMS 로그인
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })

    if not login(session):
        print('[FATAL] 로그인 실패, 종료')
        return

    # 차량번호별 검색
    total_count = 0
    all_contracts = []

    for car_num in VEHICLE_NUMBERS:
        print(f'\n[SEARCH] 차량번호: {car_num}')
        contracts = search_vehicle(session, car_num)
        print(f'  → {len(contracts)}건 발견')
        all_contracts.extend(contracts)
        total_count += len(contracts)

    # 중복 제거 (같은 IMS ID)
    seen = {}
    unique = []
    for c in all_contracts:
        if c['id'] not in seen:
            seen[c['id']] = True
            unique.append(c)

    print(f'\n[TOTAL] 총 {len(unique)}건 (중복 제거)')

    # Supabase 업데이트
    if unique:
        count = upsert_to_supabase(supabase_client, unique)
        print(f'[DB] Supabase에 {count}건 upsert 완료')
    else:
        print('[DB] 업데이트할 데이터 없음')

    print(f'\n=== 크롤링 완료: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===')


if __name__ == '__main__':
    main()
