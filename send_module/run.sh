#!/usr/bin/env bash
# Vultr cron 진입 스크립트 (예시)
#
# crontab 예시 (활성화 전엔 코멘트 처리 — LOCKED-BY-USER):
# 30 8 * * * /home/hsh/blrent-accidenterp/send_module/run.sh >> /var/log/accident_auto_send.log 2>&1
#
# 활성화는 사용자(황성현)의 명시적 "킬스위치 해제" + 코드 상수 MASTER_KILL_SWITCH=False
# + accident_send_settings.send_armed=true 가 모두 충족된 후에만.

set -e
cd "$(dirname "$0")"

# .env 로드
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

# 가상환경이 있다면
if [ -d venv ]; then
    . venv/bin/activate
fi

python3 auto_send.py
