"""既存人物提案の単発API実行。正本はassistant.json、応答は既存の不変writer。"""
import copy
import hashlib
import json
from uuid import UUID,uuid5

from manga.assistant_storage import record
from manga.character_ai_extraction import extract
from manga.character_ai_gemini import DEFAULT_MODEL,PROVIDER,SYSTEM,GeminiCharacterAI,checked_output
from manga.budget import BudgetedExecutor
from manga.budget.config import open_budget,budget_path
from manga.gemini_diagnostics import MESSAGES
from manga.models import validate_id
from manga.storage import StorageError
from manga.story_ai_gemini import check_mode,quote,GeminiTransportError
from manga.story_ai_pricing import limits,charge
from manga.story_ai_service import StoryAIService
from manga.story_ai_storage import AIStoryStore
from manga.voice_models import digest

WAITING=('sending','uncertain')
ERRORS=dict(MESSAGES,source_changed='脚本・人物・バイブルが生成時から変わっています。応答を履歴に保持し、最新の資料を確認してください。',
    invalid_output='人物JSON・根拠・対象IDを検証できません。正式人物は変更していません。応答を確認し、新しい依頼は別途承認してください。',
    not_sent='送信処理を開始せず停止しました。条件と予算を確認し、新しい依頼を明示的に承認してください。')


def text_store(service):return AIStoryStore(service.store.root.parent,service.project_id)


def sources(service,version_id,additional_names):
    latest=service.proposals.finalized(version_id);art=service.artwork.load();bibles=service.store.load()
    latest_bibles={b['character_id']:b for b in bibles['bibles']}
    bible_text=[{k:b[k] for k in ('character_id','story_version_id','profile','aliases','locked_fields','exceptions')} for b in latest_bibles.values()]
    aliases={b['character_id']:b['aliases'] for b in latest_bibles.values()}
    extraction=extract(latest['story'],art,additional_names=additional_names,aliases=aliases)
    token=digest(dict(version=latest,characters=art['characters'],bibles=bible_text,additional=list(additional_names),provider=PROVIDER,model=DEFAULT_MODEL))
    return latest,art,bible_text,extraction,token


def inspect(service,version_id,additional_names=()):
    with service.store.locked():return sources(service,version_id,additional_names)[3]


def _plan(service,project,version_id,data,*,mode,additional_names,new_request):
    if project.id!=service.project_id or type(new_request) is not bool:raise ValueError('対象作品と依頼条件を確認してください。')
    latest,art,bible_text,extraction,token=sources(service,version_id,additional_names)
    if not extraction['candidates']:raise ValueError('確実な人物候補がありません。追加名や脚本の話者を確認してください。曖昧な人物を自動同定しません。')
    store=text_store(service);text_data=store.checked(store.load()['revision']);card=text_data['card']
    if not card or card.get('provider')!=PROVIDER or card.get('model')!=DEFAULT_MODEL:
        raise ValueError('「企画・脚本 → AI企画・ストーリー」でGemini 3.5の共通料金・利用条件を確認して保存してください。')
    check_mode(card,mode);price=quote(card);bounds=limits(card)
    maximum=price.execution_evidence(PROVIDER,DEFAULT_MODEL,bounds)['computed_max_jpy']
    identifier=str(uuid5(UUID(service.project_id),digest(dict(token=token,card=card,mode=mode,new=new_request,revision=data['revision']))))
    meta=dict(provider=PROVIDER,model=DEFAULT_MODEL,mode=mode,state='sending',fingerprint=token,
        additional_names=list(additional_names),extraction=extraction,bible_text=bible_text,card=copy.deepcopy(card),
        ledger_path=str(budget_path(service.store.root.parent).resolve()),maximum_jpy=maximum,receipt_sha256=None,error=None)
    request=dict(id=identifier,project_id=service.project_id,kind='characters',story_version_id=version_id,targets=[],theme='Phase 10-A：人物の同定・採用は人間が行う。',
        evaluation_id=None,context=dict(story=copy.deepcopy(latest['story']),artwork=art,references=[]),prompt=SYSTEM,character_ai=meta)
    from manga.character_ai_gemini import payload
    approval=digest(dict(request=request,body=payload(request),revision=data['revision']))
    existing=next((r for r in reversed(data['requests']) if r.get('character_ai',{}).get('provider')==PROVIDER and r['character_ai']['fingerprint']==token),None)
    return dict(request=request,approval_key=approval,revision=data['revision'],maximum_jpy=maximum,
                existing_request_id=existing['id'] if existing and not new_request else None)


def preview(service,project,version_id,*,mode='verified-free',additional_names=(),new_request=False):
    with service.store.locked():
        data=service.proposals.checked(service.proposals.load()['revision'])
        return _plan(service,project,version_id,data,mode=mode,additional_names=additional_names,new_request=new_request)


def _ledger(service):
    directory=service.store.root.parent;path=budget_path(directory).resolve();data=service.proposals.load();stories=text_store(service).load()
    paths={r['character_ai']['ledger_path'] for r in data['requests'] if r.get('character_ai',{}).get('provider')==PROVIDER}
    paths|={j['ledger_path'] for j in stories['jobs'] if j['provider']!='mock'}
    paths|={e['ledger_path'] for e in stories['events'] if e['kind']=='gemini_connection_diagnostic'}
    if paths and paths!={str(path)}:raise ValueError('履歴と共有予算台帳の保存先が異なります。元の台帳を確認してください。')
    if paths and not path.is_file():raise ValueError('履歴の共有予算台帳が欠損しています。新規作成せずバックアップを確認してください。')
    ledger=open_budget(directory)
    for r in data['requests']:
        meta=r.get('character_ai',{})
        if meta.get('provider')==PROVIDER and meta['state'] not in ('sending','not_sent'):
            status=ledger.request_status(r['id'])
            if not status or status['project_id']!=service.project_id or status['provider']!=PROVIDER or status['model']!=DEFAULT_MODEL:
                raise ValueError('人物生成の履歴と台帳明細が一致しません。初期化せず確認してください。')
    return ledger


def budget_summary(service):return _ledger(service).summary(service.project_id)


def _result(service,identifier):
    data=service.proposals.load();request=service.proposals.request(data,identifier)
    return dict(request=request,proposal=next((p for p in data['proposals'] if p['request_id']==identifier),None))


def _state(service,identifier,state,error=None):
    with service.store.locked():
        data=service.proposals.checked(service.proposals.load()['revision']);request=service.proposals.request(data,identifier)
        request['character_ai'].update(state=state,error=error);service.proposals.save(data)
    return _result(service,identifier)


def _path(identifier):return f'story_ai/character_responses/{validate_id(identifier)}.json'


def verify_receipt(directory,project_id,request):
    content=AIStoryStore(directory,project_id).read(_path(request['id']))
    if hashlib.sha256(content).hexdigest()!=request['character_ai']['receipt_sha256']:
        raise StorageError('人物応答のハッシュが一致しません。採用せず元ファイル・バックアップを確認してください。')


def _publish(service,identifier):
    store=text_store(service);content=store.read(_path(identifier));checksum=hashlib.sha256(content).hexdigest()
    receipt=json.loads(content)
    with service.store.locked():
        data=service.proposals.checked(service.proposals.load()['revision']);request=service.proposals.request(data,identifier);meta=request['character_ai']
        if receipt['request_id']!=identifier or receipt['source_token']!=meta['fingerprint'] or meta['receipt_sha256'] not in (None,checksum):
            raise StorageError('人物応答のID・版・ハッシュを照合できません。再送せず保存先を確認してください。')
        old=next((p for p in data['proposals'] if p['request_id']==identifier),None)
        if meta['state']=='saved' and old:return dict(request=request,proposal=old)
        try:
            current=sources(service,request['story_version_id'],meta['additional_names'])[-1]
            if current!=meta['fingerprint']:raise ValueError
        except (ValueError,StorageError):
            meta.update(state='stale',error='source_changed',receipt_sha256=checksum);service.proposals.save(data)
            return dict(request=request,proposal=None)
        try:payload=checked_output(receipt['response'],request)
        except (ValueError,TypeError,KeyError,RecursionError):
            meta.update(state='invalid',error='invalid_output',receipt_sha256=checksum);service.proposals.save(data)
            return dict(request=request,proposal=None)
        if old and old['original_payload']!=payload:raise StorageError('同じ依頼に異なる提案が保存されています。上書きせず確認してください。')
        proposal=old or record(request_id=identifier,payload=payload,original_payload=copy.deepcopy(payload),edits=[],decisions={i['id']:'確認待ち' for i in payload['items']})
        if not old:data['proposals'].append(proposal)
        meta.update(state='saved',error=None,receipt_sha256=checksum);service.proposals.save(data)
        return dict(request=request,proposal=proposal)


def generate(service,project,version_id,*,approval,expected_revision,mode='verified-free',additional_names=(),new_request=False,adapter=None):
    if not StoryAIService.api_enabled(mode):raise ValueError('Gemini実送信は既定OFFです。既存のテキストAPI機能フラグと利用条件を確認してください。')
    store=text_store(service)
    with store.lock(generation=True):
        with service.store.locked():
            data=service.proposals.checked(expected_revision)
            plan=_plan(service,project,version_id,data,mode=mode,additional_names=additional_names,new_request=new_request)
            if approval!=plan['approval_key']:raise ValueError('この作品・脚本・人物・送信内容・最大費用の1回限りの承認が必要です。')
            if any(r.get('character_ai',{}).get('provider')==PROVIDER and r['character_ai']['state'] in WAITING for r in data['requests']):
                raise ValueError('人物生成が処理中・結果不明です。保存・請求を照合するまで再送しません。')
            if plan['existing_request_id']:return _result(service,plan['existing_request_id'])
            request=plan['request'];meta=request['character_ai'];identifier=request['id'];adapter=adapter or GeminiCharacterAI();adapter.prepare(request)
            ledger=_ledger(service)
            if not BudgetedExecutor(ledger).dry_run(project_id=service.project_id,quote_jpy=plan['maximum_jpy'],provider=PROVIDER)['possible']:
                raise ValueError('作品500円・全作品月間5,000円の残額不足、または請求確認待ちのため送信できません。')
            store.write(f'story_ai/character_responses/{identifier}.probe',b'probe')
            request['saved_at']=record()['saved_at'];data['requests'].append(request);service.proposals.save(data)
        started=False
        def operation():
            nonlocal started
            started=True;response=adapter.generate(request)
            content=json.dumps(dict(request_id=identifier,source_token=meta['fingerprint'],response=response),ensure_ascii=False,allow_nan=False).encode()
            store.write(_path(identifier),content)
            return response
        try:
            BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=identifier,provider=PROVIDER,model=DEFAULT_MODEL,kind='character-settings',
                price_quote=quote(meta['card']),usage_limits=limits(meta['card']),operation=operation,actual_charge_jpy=lambda response:charge(meta['card'],response))
        except BaseException as error:
            status=ledger.request_status(identifier)
            state='not_sent' if not started and status is None else 'uncertain'
            category=error.category if isinstance(error,GeminiTransportError) and error.category in ERRORS else 'not_sent' if state=='not_sent' else 'interrupted'
            try:return _state(service,identifier,state,category)
            except (ValueError,OSError,StorageError):raise ValueError('人物生成の保存・請求を確認してください。自動再送しません。') from None
        return _publish(service,identifier)


def recover(service,identifier):
    validate_id(identifier);store=text_store(service)
    with store.lock(generation=True):
        store.checked(store.load()['revision'])
        result=_result(service,identifier);meta=result['request'].get('character_ai',{})
        if meta.get('provider')!=PROVIDER:raise ValueError('Gemini人物生成の復旧対象を選択してください。')
        ledger=_ledger(service);status=ledger.request_status(identifier)
        if not status or status['project_id']!=service.project_id or status['provider']!=PROVIDER or status['model']!=DEFAULT_MODEL:
            raise ValueError('元の人物生成リクエストと予算台帳を照合できません。再送・自動精算しません。')
        if status['state']!='settled':raise ValueError('請求が未確定です。既存のAPI予算管理で明細を照合してから保存を再開してください。')
        if not store.safe(_path(identifier)).is_file():
            if meta['state']=='saved':raise StorageError('保存済み人物応答が欠損しています。既存提案を上書きせずバックアップを確認してください。')
            return _state(service,identifier,'invalid','invalid_output')
        return _publish(service,identifier)
