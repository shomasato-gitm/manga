"""送信対象、個別同意、版、予算保存先を見積と実行の両方で検証する。"""
import hashlib
from pathlib import Path

from manga.budget.config import budget_path, open_budget
from manga.budget import BudgetedExecutor
from manga.character_ai_assets import reference_path
from manga.image_ai_live_pricing import quotation
from manga.image_ai_live_provider import request_body
from manga.image_ai_planning import current
from manga.video_sources import file_bytes
from manga.voice_models import digest


def shared_ledger(service,*,expected=None):
    path=str(budget_path(service.directory).resolve())
    if expected is not None and expected!=path: raise ValueError('生成時と共有台帳の保存先が異なります。元の台帳を確認してください。')
    # 既存の他工程が同作品について使った台帳を変更して0円から再開しない。
    from manga.story_ai_storage import AIStoryStore
    from manga.voice_storage import VoiceStore
    paths={j['live']['ledger_path'] for j in service.store.load()['jobs'] if j['provider']=='gemini-live'}
    paths.update(j['ledger_path'] for j in AIStoryStore(service.directory,service.project_id).load()['jobs'] if j['provider']!='mock')
    paths.update(j['ledger_path'] for j in VoiceStore(service.directory,service.project_id).load()['jobs'] if j['settings']['provider']!='mock')
    if paths and paths!={path}: raise ValueError('脚本・音声・画像の台帳保存先が一致しません。新規DBへ切り替えず元の台帳を確認してください。')
    return open_budget(service.directory)


def prepare(service,plan,card,consents):
    service.live_adapter.check()
    if not service.live_adapter.fake:
        from manga.image_ai_live_pricing import research
        if card.get('model')!=research()['model']: raise ValueError('このモデルの実送信能力は確認されていません。')
    current(service.directory,service.project_id,plan,hashed=True)
    if hashlib.sha256(plan['prompt'].encode()).hexdigest()!=plan['prompt_sha256']: raise ValueError('プロンプトが変更されています。再計画してください。')
    quoted=quotation(card)
    if any(j.get('error_code')=='usage_bounds_exceeded' for j in service.store.load()['jobs']): raise ValueError('過去の画像応答が最大利用量を超えています。料金根拠を再検証するまで新規送信を停止します。')
    if plan['options']['size']!=f"{card['width']}x{card['height']}": raise ValueError('計画サイズと料金カードの対応寸法が一致しません。')
    refs={r['id']:r for r in plan['reference_images']}
    if not isinstance(consents,list) or len(consents)!=len(set(consents)) or set(consents)!=set(refs): raise ValueError('使用する各参照画像について外部送信に個別同意してください。未同意の画像は送りません。')
    if len(refs)>card['max_reference_images']: raise ValueError('参照画像枚数が確認済みの上限を超えています。')
    inputs=[]
    for identifier in sorted(consents):
        ref=refs[identifier]; reference_path(service.store.root,ref)
        raw=file_bytes(service.store.root,ref['path'],card['max_reference_bytes'])
        inputs.append((ref,raw))
    body=request_body(plan,card,inputs)
    current(service.directory,service.project_id,plan,hashed=True)
    quoted.update(input_sha256=hashlib.sha256(body).hexdigest(),consents=sorted(consents),material_token=plan['material_token'],fake=service.live_adapter.fake,
                  quality='provider-default',max_images=1,retry_limit=0)
    quoted['approval_token']=digest(quoted)
    ledger=shared_ledger(service)
    quoted['ledger_path']=str(ledger.path.resolve())
    quoted['budget']=BudgetedExecutor(ledger).dry_run(project_id=service.project_id,quote_jpy=quoted['max_jpy'],provider='gemini-live')
    # UI用Decimalを保存可能な文字列にする。
    quoted['budget']={k:str(v) if hasattr(v,'as_tuple') else v for k,v in quoted['budget'].items()}
    return quoted,body


def validate_live_job(job):
    from manga.image_ai_live_pricing import validate_card
    from manga.image_ai_models import sha
    live=job['live']; validate_card(live['card'])
    if live['quality']!='provider-default' or type(live['max_images']) is not int or live['max_images']!=1 or type(live['retry_limit']) is not int or live['retry_limit']!=0: raise ValueError('品質・枚数・再試行の凍結条件が不正です。')
    if type(live['fake']) is not bool or type(job['api_calls']) is not int or not 0<=job['api_calls']<=1: raise ValueError('API実行記録が不正です。')
    for key in ('input_sha256','approval_token','card_sha256'): sha(live[key])
    if digest(live['card'])!=live['card_sha256'] or not Path(live['ledger_path']).is_absolute(): raise ValueError('料金・台帳の識別情報が不正です。')
    if live['card']['provider']!='gemini-live' or set(live['consents'])!={r['id'] for r in job['plan']['reference_images']} or len(live['consents'])!=len(set(live['consents'])): raise ValueError('参照画像同意の記録が不正です。')
    from manga.budget.pricing import _amount
    _amount(job['cost_jpy'],'精算額'); _amount(live['max_jpy'],'最大予約額')
