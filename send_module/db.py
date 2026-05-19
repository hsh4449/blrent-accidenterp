"""Supabase 클라이언트 + KST 헬퍼"""
import os
from datetime import datetime, timezone, timedelta
from supabase import create_client

KST = timezone(timedelta(hours=9))


def get_client():
    return create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_KEY'])


def kst_today():
    return datetime.now(KST).date()


def kst_now_iso():
    return datetime.now(KST).isoformat()
