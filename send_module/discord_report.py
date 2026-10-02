"""
매일 자동 독촉이 끝난 뒤 디스코드 '비엘매니저 관리' 채널로 요약 보고 (사용자 지시 2026-10-02).

내용: 독촉 문자 발송/실패 통수(솔라피 접수 기준), 결과 보고 문자 통수, 솔라피 잔액.
  - 채널 웹훅 URL 은 Railway 변수 DISCORD_WEBHOOK_URL. 없으면 보고만 건너뜀.
  - 이 모듈의 어떤 실패도 독촉 발송에 영향 주지 않음 (예외는 전부 print 후 무시).
"""
import os

import requests

from solapi_sender import _auth_header

DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', '')
BALANCE_URL = 'https://api.solapi.com/cash/v1/balance'

OWNER_NAME = {'hq': '본사', 'jiip': '신동석', 'jang': '장명재', 'kim': '김민규', 'choi': '최제렬',
              'jeon': '전상민', 'yu': '유윤빈', 'kang': '강연수', 'han': '한정규'}


def solapi_balance() -> str:
    """솔라피 잔액 문자열. 조회 실패 시 '조회 실패 (사유)'."""
    try:
        r = requests.get(BALANCE_URL, headers={'Authorization': _auth_header()}, timeout=15)
        if r.status_code != 200:
            return f'조회 실패 (HTTP {r.status_code})'
        b = r.json()
        text = f'{round(b.get("balance") or 0):,}원'
        if b.get('point'):
            text += f' (포인트 {round(b["point"]):,}원 별도)'
        return text
    except Exception as e:
        return f'조회 실패 ({type(e).__name__})'


def report_counts(sb, run_date: str) -> tuple:
    """오늘 결과 보고 문자 (접수 성공 통수, 그 외 통수)."""
    rows = (sb.table('accident_result_reports').select('status')
              .eq('run_date', run_date).execute().data) or []
    ok = sum(1 for r in rows if r.get('status') == 'registered')
    return ok, len(rows) - ok


def build_text(run_date: str, results: dict, errors: dict, report_ok: int, report_bad: int,
               balance: str, holiday: bool = False) -> str:
    """results: owner → send_plan 결과, errors: owner → 오류 문자열."""
    lines = [f'**[사고대차 자동 독촉] {run_date}**']
    if holiday:
        lines.append('일요일이라 발송 없음')
    else:
        sent = sum(r.get('sent', 0) for r in results.values())
        failed = sum(r.get('failed', 0) for r in results.values())
        lines.append(f'독촉 문자 발송 {sent}통 / 실패 {failed}통')
        if results:
            lines.append('· ' + ', '.join(f'{OWNER_NAME.get(o, o)} {r.get("sent", 0)}'
                                          + (f'(실패 {r["failed"]})' if r.get('failed') else '')
                                          for o, r in results.items()))
        lines.append(f'결과 보고 문자 발송 {report_ok}통 / 실패·확인중 {report_bad}통')
        for o, msg in errors.items():
            lines.append(f'⚠ {OWNER_NAME.get(o, o)} 처리 오류: {msg}')
    lines.append(f'솔라피 잔액 {balance}')
    return '\n'.join(lines)


def post_daily(sb, today, results: dict, errors: dict, holiday: bool = False) -> None:
    if not DISCORD_WEBHOOK_URL:
        print('[DISCORD] DISCORD_WEBHOOK_URL 미설정 → 보고 건너뜀')
        return
    try:
        run_date = today.isoformat()
        report_ok, report_bad = (0, 0) if holiday else report_counts(sb, run_date)
        text = build_text(run_date, results, errors, report_ok, report_bad, solapi_balance(), holiday)
        r = requests.post(DISCORD_WEBHOOK_URL, json={'content': text}, timeout=15)
        print(f'[DISCORD] 보고 HTTP {r.status_code}')
    except Exception as e:
        print(f'[DISCORD] 보고 실패: {type(e).__name__}: {e}')


if __name__ == '__main__':
    # 연결 확인용: 문자 발송 없이 테스트 메시지 1통 (웹훅·솔라피 잔액 조회 확인). Railway Console: python send_module/discord_report.py
    if not DISCORD_WEBHOOK_URL:
        print('DISCORD_WEBHOOK_URL 미설정')
    else:
        r = requests.post(DISCORD_WEBHOOK_URL, timeout=15, json={
            'content': f'**[사고대차 자동 독촉] 연결 테스트**\n문자 발송 없음 — 매일 08:30 자동 독촉 후 이 채널로 요약이 옵니다.\n솔라피 잔액 {solapi_balance()}'})
        print(f'디스코드 HTTP {r.status_code}')
