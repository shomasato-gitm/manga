"""公式根拠と不足条件の読取専用診断。カード自己申告で本番を解禁しない。"""
import copy
import hashlib
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED = {'model','one-image','references','input-bound','output-bound','thinking-bound','pricing','usage','failure-billing','terms'}
REPORT = Path(__file__).resolve().parents[1]/'examples/image_ai/gemini_readiness_20261024.json'


def _official_url(value,scope):
    if not isinstance(value,str): raise ValueError('公式資料URLが不正です。')
    url=urlsplit(value)
    domains={'developer-api':{'ai.google.dev'},'sdk':{'raw.githubusercontent.com'},
             'vertex':{'cloud.google.com','docs.cloud.google.com'},'cloud-billing':{'cloud.google.com','docs.cloud.google.com'}}
    if scope not in domains or url.scheme!='https' or url.hostname not in domains[scope] or url.username or url.password or url.query or any(c.isspace() for c in value): raise ValueError('公式資料の対象サービス・URLを確認してください。')
    if scope=='sdk' and not url.path.startswith('/googleapis/python-genai/'): raise ValueError('SDKの公式所有者を確認してください。')


def load_evidence():
    try:
        if REPORT.is_symlink() or REPORT.stat().st_size>128*1024: raise ValueError
        return json.loads(REPORT.read_text(encoding='utf-8'))
    except (OSError,ValueError,RecursionError): raise ValueError('本番接続の公式確認記録を読み込めません。送信禁止を維持してリポジトリの資料を復元してください。') from None


def assess(raw=None,*,today=None):
    """根拠の確認状態のみを判定する。ネットワーク・作品・台帳へのアクセスなし。"""
    try:
        raw=copy.deepcopy(load_evidence() if raw is None else raw)
        keys={'schema_version','format','model','verified_on','sources','facts','requirements','reference_cost'}
        if not isinstance(raw,dict) or set(raw)!=keys or type(raw['schema_version']) is not int or raw['schema_version']!=1 or raw['format']!='manga-gemini-readiness': raise ValueError
        from manga.image_ai_live_pricing import research
        if raw['model']!=research()['model']: raise ValueError('調査対象モデルが現在の実装と異なります。')
        checked=date.fromisoformat(raw['verified_on'])
        if checked.isoformat()!=raw['verified_on']: raise ValueError
        expired=not 0<=((today or date.today())-checked).days<=30
        sources={s['id']:s for s in raw['sources']}
        if not sources or len(sources)!=len(raw['sources']): raise ValueError
        for source in sources.values():
            _official_url(source['url'],source['scope'])
            if type(source['retrieved']) is not bool or source['checked_on']!=raw['verified_on']: raise ValueError
            if source['retrieved']:
                _official_url(source['final_url'],source['scope'])
                from manga.image_ai_models import sha
                sha(source['sha256'])
            elif source['sha256'] is not None: raise ValueError
        requirements=raw['requirements']
        if {r['id'] for r in requirements}!=REQUIRED or len(requirements)!=len(REQUIRED): raise ValueError('必要な確認項目が欠損または重複しています。')
        for row in requirements+raw['facts']:
            if row['status'] not in ('confirmed','unverified') or not isinstance(row['source_ids'],list) or not row['source_ids'] or len(set(row['source_ids']))!=len(row['source_ids']): raise ValueError
            if any(i not in sources for i in row['source_ids']): raise ValueError
            if row['status']=='confirmed' and any(not sources[i]['retrieved'] for i in row['source_ids']): raise ValueError('未取得の資料を確認済みにできません。')
        for row in requirements:
            if row['status']=='confirmed' and any(sources[i]['scope']!='developer-api' for i in row['source_ids']): raise ValueError('別サービス・SDKの情報をDeveloper APIの課金根拠にはできません。')
        for fact in raw['facts']:
            if any(sources[i]['scope']!=fact['scope'] for i in fact['source_ids']): raise ValueError
        reference=raw['reference_cost']
        if reference['scope']!='vertex-output-only-not-developer-quote' or reference['maximum_jpy'] is not None: raise ValueError('参考費用を最大費用として扱いません。')
        usd=Decimal(str(reference['output_tokens']))*Decimal(reference['usd_per_million'])/1000000
        if Decimal(reference['usd'])!=usd or Decimal(reference['jpy'])!=usd*Decimal(reference['fx_jpy_per_usd'])*Decimal(reference['tax_multiplier']): raise ValueError('参考料金の計算が一致しません。')
        blockers=[dict(id=r['id'],title=r['title'],needed=r['needed']) for r in requirements if r['status']!='confirmed']
        if expired: blockers.append(dict(id='expired',title='資料の再確認',needed='30日以内の公式再確認日・取得SHAを記録してください。'))
        return dict(model=raw['model'],verified_on=raw['verified_on'],evidence_ready=not blockers,decision='evidence-ready' if not blockers else 'no-go',
                    blockers=blockers,facts=raw['facts'],sources=raw['sources'],reference_cost=reference,maximum_jpy=None,
                    evidence_sha256=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True).encode()).hexdigest())
    except (KeyError,TypeError,AttributeError,ValueError,ArithmeticError,RecursionError):
        raise ValueError('本番接続の確認記録が不正です。確認項目・公式出典・日付・参考料金を見直してください。送信は禁止のままです。') from None


def diagnostic():
    """設定値やキーの文字列・長さ・ハッシュを出力しない。予算DBも作らない。"""
    result=assess()
    from manga.image_ai_live_pricing import NETWORK_VERIFIED,research
    old=research()
    approved=NETWORK_VERIFIED is True and old['network_enabled'] is True and old['maximum_charge_verified'] is True
    result.update(code_review_approved=approved,feature_flag_enabled=os.environ.get('MANGA_ENABLE_IMAGE_API')=='1',
                  authentication_configured=bool(os.environ.get('GEMINI_API_KEY','').strip()),
                  production_enabled=False,api_calls=0,budget_reservations=0)
    # これは公開前の資料診断であり、作品・料金カード・参照画像の個別承認を代行しない。
    result['global_prerequisites_met']=all((result['evidence_ready'],approved,result['feature_flag_enabled'],result['authentication_configured']))
    return result
