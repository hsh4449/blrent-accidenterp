"""
솔라피(Solapi) SMS/LMS 발송 헬퍼.

직접 REST 호출 (HMAC-SHA256 인증).
- API: POST https://api.solapi.com/messages/v4/send-many/detail
- 90 byte (EUC-KR 환산) 초과 시 LMS

(blrent-jiip-claims/solapi_sender.py 와 동일 패턴)
"""
import os
import hmac
import hashlib
import uuid
from datetime import datetime, timezone

SOLAPI_API_KEY = os.environ.get('SOLAPI_API_KEY', '')
SOLAPI_API_SECRET = os.environ.get('SOLAPI_API_SECRET', '')
SOLAPI_FROM = os.environ.get('SOLAPI_FROM', '')

API_URL = 'https://api.solapi.com/messages/v4/send-many/detail'


def _auth_header():
    date = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    salt = uuid.uuid4().hex
    msg = (date + salt).encode()
    sig = hmac.new(SOLAPI_API_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return f'HMAC-SHA256 apiKey={SOLAPI_API_KEY}, date={date}, salt={salt}, signature={sig}'


def byte_len(s: str) -> int:
    """EUC-KR 환산 길이 (한글 2byte, ASCII 1byte) — 솔라피 SMS/LMS 판단용"""
    n = 0
    for ch in s:
        n += 2 if ord(ch) > 127 else 1
    return n
