"""Railway 워커 — Vultr crontab 에 있던 사고대차 작업 3개를 프로세스 하나로 (2026-09-06 이전).

기존 스크립트를 그대로 서브프로세스로 실행하므로 동작은 cron 과 동일하다.
  - 매일 08:30 KST  send_module/auto_send.py            (자동 독촉, 일요일 제외 게이트는 스크립트 안에 있음)
  - 2분마다        send_module/process_manual_queue.py  (ERP 화면 [문자발송]/[전체발송] 큐 처리)
  - 09~17시 17분   crawler.py                           (IMS 크롤 → accident_rentals upsert)
시작 시 큐 처리를 한 번 돌려 env·Supabase 연결을 확인한다 (`--once` 면 크롤까지 1회 실행 후 종료).

환경변수: send_module/.env.example 의 키 전부 + TZ=Asia/Seoul (Railway 변수). 로컬 테스트: python send_module/worker.py --once
"""
import os
import sys
import subprocess
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
KST = timezone(timedelta(hours=9))


def run(name, script, cwd):
    started = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[worker] ▶ {name} 시작 {started} KST", flush=True)
    r = subprocess.run([PY, script], cwd=cwd)          # 출력은 그대로 Railway 로그로
    print(f"[worker] ■ {name} 종료 exit={r.returncode}", flush=True)
    return r.returncode


def auto_send():
    return run("auto_send", os.path.join(HERE, "auto_send.py"), HERE)


def manual_queue():
    return run("manual_queue", os.path.join(HERE, "process_manual_queue.py"), HERE)


def crawler():
    return run("crawler", os.path.join(ROOT, "crawler.py"), ROOT)


if __name__ == "__main__":
    print(f"[worker] 기동 {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST / TZ={os.environ.get('TZ')} / python {sys.version.split()[0]}", flush=True)
    # 기동 점검: 큐 처리 1회 (빈 큐면 조용히 종료). 크롤은 스케줄에만 — 재시작마다 IMS 로그인 반복 방지
    manual_queue()
    if "--once" in sys.argv:
        crawler()
        sys.exit(0)

    sched = BlockingScheduler(timezone="Asia/Seoul", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    sched.add_job(auto_send, CronTrigger(hour=8, minute=30, timezone="Asia/Seoul"), id="auto_send")
    sched.add_job(manual_queue, CronTrigger(minute="*/2", timezone="Asia/Seoul"), id="manual_queue")
    sched.add_job(crawler, CronTrigger(hour="9-17", minute=17, timezone="Asia/Seoul"), id="crawler")
    now = datetime.now(KST)
    for j in sched.get_jobs():   # 시작 전엔 next_run_time 속성이 없어 trigger 로 직접 계산 (APScheduler 3.x)
        print(f"[worker] 예약 {j.id:13s} 다음 실행 {j.trigger.get_next_fire_time(None, now):%Y-%m-%d %H:%M %Z}", flush=True)
    sched.start()
