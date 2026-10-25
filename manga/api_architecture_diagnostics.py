"""Phase 9-A: research-only/offline diagnostics. No transport, Store or ledger writes."""
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

EVIDENCE = Path(__file__).resolve().parents[1]/'docs/phase9-provider-evidence.json'
PROVIDERS = {'gemini-text','openai-text','chatgpt-text','gemini-image','openai-image',
             'chatgpt-image','voicevox','aws-polly','manual-voice','mock'}
REQUIREMENTS = {'availability','capabilities','maximum_charge','terms_for_this_use','single_approval','account_permissions'}
HOSTS = {'ai.google.dev','developers.openai.com','openai.com','voicevox.hiroshiba.jp',
         'zunko.jp','aws.amazon.com','docs.aws.amazon.com','github.com'}


def voicevox_origin_allowed(origin):
    """設計上の許可候補だけを判定。名前解決・接続・URL補正をしない。"""
    return type(origin) is str and origin in ('http://127.0.0.1:50021','http://[::1]:50021')


def _official_url(value):
    if not isinstance(value,str) or any(c.isspace() for c in value):
        raise ValueError
    u = urlsplit(value)
    if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password or u.query or u.fragment or u.port is not None:
        raise ValueError
    if u.hostname=='github.com' and not u.path.startswith('/VOICEVOX/voicevox_engine'):
        raise ValueError


def assess_evidence(raw, *, today=None):
    """不足・未取得・未来/期限切れを拒否。全項目を確認済みに変えても実行権は出さない。"""
    try:
        if type(raw['schema_version']) is not int or raw['schema_version']!=1 or raw['format']!='manga-phase9-research-only':
            raise ValueError
        checked = date.fromisoformat(raw['checked_on'])
        if checked.isoformat()!=raw['checked_on']:
            raise ValueError
        # 調査日の表記と同じ日本時間で比較する。UTCの前日を未来日と誤判定しない。
        current_day = today or datetime.now(timezone(timedelta(hours=9))).date()
        fresh = 0<=(current_day-checked).days<=30
        sources = {s['id']:s for s in raw['sources']}
        if not sources or len(sources)!=len(raw['sources']):
            raise ValueError
        for source in sources.values():
            _official_url(source['url'])
            if type(source['retrieved']) is not bool or source['checked_on']!=raw['checked_on']:
                raise ValueError
        rows = raw['providers']
        if {r['id'] for r in rows}!=PROVIDERS or len(rows)!=len(PROVIDERS):
            raise ValueError
        result = []
        for row in rows:
            if row['mode'] not in ('cloud','local','manual','mock') or not isinstance(row['facts'],dict):
                raise ValueError
            requirements = row['execution_requirements']
            if set(requirements)!=REQUIREMENTS or any(v not in ('confirmed','unverified') for v in requirements.values()):
                raise ValueError
            blockers = sorted(k for k,v in requirements.items() if v!='confirmed')
            for fact in row['facts'].values():
                refs = fact['source_ids']
                if not isinstance(refs,list) or not refs or len(set(refs))!=len(refs) or any(i not in sources for i in refs):
                    raise ValueError
                if any(not sources[i]['retrieved'] for i in refs):
                    blockers.append('official-source-unavailable')
            if not fresh:
                blockers.append('evidence-expired-or-future')
            if not row['facts'] and row['mode'] in ('cloud','local'):
                blockers.append('official-evidence-missing')
            if row['mode']=='cloud' and row['maximum_jpy'] is None:
                blockers.append('maximum-charge-unknown')
            # これは接続テストではない。キー・flags・JSONから実送信可能へ昇格させない。
            result.append(dict(provider=row['id'],mode=row['mode'],decision='not-connectable-in-phase9a',
                               existing_manual_or_mock_available=row['id'] in ('chatgpt-text','chatgpt-image','mock'),
                               blockers=sorted(set(blockers)),phase9a_execution_enabled=False,maximum_jpy=None))
        return dict(checked_on=raw['checked_on'],fresh=fresh,providers=result,
                    phase9a_execution_enabled=False,network_calls=0,budget_reservations=0)
    except (KeyError,TypeError,ValueError,AttributeError,RecursionError):
        # 未信頼のJSON/例外本文を返さない。キー等の誤混入も診断ログへ転記しない。
        raise ValueError('公式調査記録が不正です。送信せず、資料の形式・出典・日付を確認してください。') from None


def diagnostic(*, environ=None, today=None):
    """キーの設定有無のみ。作品・DB・ソケット・環境全体を読み出さない。"""
    if EVIDENCE.is_symlink() or EVIDENCE.stat().st_size>128*1024:
        raise ValueError('公式調査記録を安全に読み込めません。送信は禁止のままです。')
    result = assess_evidence(json.loads(EVIDENCE.read_text(encoding='utf-8')),today=today)
    env = os.environ if environ is None else environ
    def present(name):
        return bool(env.get(name,'').strip())
    result['credential_presence'] = dict(openai=present('OPENAI_API_KEY'),gemini=present('GEMINI_API_KEY'),
                                        aws=present('AWS_ACCESS_KEY_ID') and present('AWS_SECRET_ACCESS_KEY'))
    result['existing_flags'] = {name:env.get(name)=='1' for name in ('MANGA_ENABLE_PAID_STORY','MANGA_ENABLE_PAID_TTS','MANGA_ENABLE_IMAGE_API')}
    from manga.image_ai_live_pricing import NETWORK_VERIFIED
    result['existing_gemini_network_verified'] = NETWORK_VERIFIED
    result['budget'] = dict(project_lifetime_limit_jpy=500,all_projects_monthly_limit_jpy=5000,
                            remaining_jpy=None,state='not-read-no-ledger-created')
    result['note'] = '既存アプリの実行設定は変更していません。診断の無効表示はアプリ全体の遮断を意味しません。'
    return result
