"""Supabase 클라이언트 + KST 헬퍼"""
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from supabase import create_client

KST = timezone(timedelta(hours=9))

# .env 자동 로드 — run.sh 거치지 않고 venv python 직접 호출 시에도 환경변수 채워줌.
# (이미 export 된 경우엔 setdefault 라 덮어쓰지 않음)
_env_path = Path(__file__).parent / '.env'
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if '=' in _line and not _line.startswith('#'):
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())


def get_client():
    return create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])


def kst_today():
    return datetime.now(KST).date()


def kst_now_iso():
    return datetime.now(KST).isoformat()
