"""
보고를 카톡(비엘관리방)으로도 보내기 — BL매니저 카톡 발송함 API 에 적재 (사용자 지시 2026-10-14).

  - Railway 변수 KAKAO_OUTBOX_URL (예: https://invigorating-creativity-production-755c.up.railway.app)
    + KAKAO_OUTBOX_TOKEN (BL매니저 AUTOMATION_TOKEN). 둘 중 하나라도 없으면 건너뜀.
  - 집 PC 봇(kakao-bridge)이 발송함을 꺼내 카톡방에 올린다.
  - 어떤 실패도 독촉·크롤에 영향 주지 않음 (예외는 print 후 무시).
"""
import os

import requests


def push(text: str, source: str = 'accident') -> None:
    base = os.environ.get('KAKAO_OUTBOX_URL', '').rstrip('/')
    token = os.environ.get('KAKAO_OUTBOX_TOKEN', '')
    if not base or not token:
        print('[KAKAO] KAKAO_OUTBOX_URL/TOKEN 미설정 → 카톡 보고 건너뜀', flush=True)
        return
    try:
        r = requests.post(f'{base}/api/kakao/outbox', json={'text': text[:1900], 'source': source},
                          headers={'Authorization': f'Bearer {token}'}, timeout=15)
        print(f'[KAKAO] 발송함 적재 HTTP {r.status_code}', flush=True)
    except Exception as e:
        print(f'[KAKAO] 발송함 적재 실패: {type(e).__name__}: {e}', flush=True)
