"""初回接続を段階的に診断する。ローカル診断は完全に読み取り専用。"""
import copy
import hashlib
import json
from uuid import uuid4

from manga.budget import BudgetedExecutor
from manga.budget.config import open_budget
from manga.budget.guard import claim_provider_call,current_execution
from manga.models import validate_id
from manga.storage import StorageError
from manga.story import load_json_payload
from manga.story_ai_gemini import MODEL,INPUT_LIMIT,FLAGS,template,api_key,diagnostic_quote,check_free_tier,parse_response,GeminiTransportError
from manga.story_ai_pricing import charge_from_usage
from manga.story_ai_storage import AIStoryStore
from manga.voice_models import digest

PROVIDER='gemini-text'
EVENT='gemini_connection_diagnostic'
LIMITS=dict(max_input_tokens=INPUT_LIMIT,max_output_tokens=256,max_items=0,retry_limit=0)
PROMPT='Connection diagnostic only. Return exactly this JSON: {"ok":true}'
MESSAGES={
    'forbidden':'403：このキー・プロジェクトにモデル利用権限がないか、APIキーの制限、地域・組織ポリシー等で拒否されました。Google AI Studioで対象プロジェクトとモデルの提供条件を確認してください。',
    'not_found':'404：このモデルが提供されていない、IDが無効、またはこのアカウントから利用できない可能性があります。公式モデル案内とアカウントを確認してください。別モデルへ自動変更はしません。',
    'rate_limited':'429：利用回数・トークン数・無料枠・一時的な容量などの上限が考えられます。利用枠と稼働状況を確認してください。有料への自動切替・自動再送はしません。',
    'unauthorized':'401：認証を確認できません。MacのAPIキー設定と、キーを発行したプロジェクトを確認してください。キーの値を画面へ貼り付けないでください。',
    'bad_request':'400：診断パラメーターを受け付けられませんでした。公式API仕様とアプリの版を確認してください。自動で条件を変えて再送しません。',
    'server_error':'提供元でエラーが発生しました。送信結果と請求を確認してください。',
    'network_error':'通信・タイムアウト等により結果が不明です。接続環境とGoogle側の利用明細を確認するまで再送しません。',
    'invalid_response':'応答の形式・モデル・利用量を確認できません。診断成功とは扱わず、請求と保存状態を確認してください。',
    'http_error':'APIが診断を受け付けませんでした。Google側の利用状況と請求を確認してください。',
    'interrupted':'診断が中断しました。保存済み記録と請求を照合してください。再送は行いません。',
    'storage_error':'応答の保存が完了しませんでした。保存先・容量と請求を照合し、保存済み応答から再開してください。APIは再送しません。',
    'not_sent':'予算台帳にこの診断の予約・実行記録がなく、送信前に停止しています。履歴を残し、新しい見積を個別承認して診断できます。',
}


class DiagnosticStorageError(ValueError):pass


def probe_body():
    return dict(contents=[dict(role='user',parts=[dict(text=PROMPT)])],generationConfig=dict(candidateCount=1,maxOutputTokens=256,
        thinkingConfig=dict(thinkingBudget=0),responseMimeType='application/json',responseJsonSchema=dict(type='object',properties=dict(ok=dict(type='boolean')),required=['ok'],additionalProperties=False)))


def parse_card(raw):
    if isinstance(raw,dict):return copy.deepcopy(raw)
    if isinstance(raw,bytes):raw=raw.decode('utf-8-sig')
    if not isinstance(raw,str) or len(raw.encode())>65536:raise ValueError('設定JSONはUTF-8・64KiB以下にしてください。')
    value=load_json_payload(raw)
    if not isinstance(value,dict):raise ValueError('設定はJSONオブジェクトにしてください。')
    return value


def offline_report(raw=None):
    """キーの存在/形式だけを返す。モデル・無料枠の実際の権限は確認しない。"""
    import os
    key_set=bool(os.environ.get('GEMINI_API_KEY'))
    try:api_key();key_format=True
    except ValueError:key_format=False
    result=dict(key_set=key_set,key_format=key_format,json_status='未確認',model_information='未確認',
        account_access='未確認',free_tier='未確認',send_ready=False,missing=[],network_calls=0,budget_reservations=0)
    try:card=parse_card(raw)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        result['missing']=['設定JSONが未入力、または形式が不正です。ファイルを選択するか、JSONを貼り付けてください。'];return result
    result['json_status']='形式確認済み'
    if set(card)!=set(template()):result['missing'].append('設定項目に不足・余分な項目があります。Geminiのひな形と比較してください。')
    if card.get('model')==MODEL:
        result['model_information']='確認済み（2026-10-26時点の同梱の公式資料。現在の提供・アカウント権限は未確認）'
    else:result['missing'].append('この版で公式確認したモデルIDは gemini-2.5-flash-lite です。')
    if card.get('account_access_reviewed') is True:result['account_access']='確認済み（本人申告・通信未検証）'
    try:check_free_tier(card);result['free_tier']='確認済み（本人申告・実際の利用権限は未確認）'
    except ValueError:result['missing'].append('無料枠の適用・残枠・課金設定の本人確認は未確認です。AI Studioの表示だけではモデル権限は確認できません。')
    for flag in FLAGS:
        if flag!='account_access_reviewed' and card.get(flag) is not True:
            result['missing'].append(dict(availability_reviewed='公式モデル提供情報',structured_outputs_reviewed='JSON出力のAPI仕様',commercial_use_reviewed='利用規約・作品の利用目的',maximum_billing_reviewed='最大課金量と料金',external_data_reviewed='外部送信とデータ利用条件')[flag]+'を確認してください。')
    try:
        diagnostic_quote(card).execution_evidence(PROVIDER,MODEL,LIMITS);result['send_ready']=key_format
    except (ValueError,TypeError,ArithmeticError):result['missing'].append('料金確認日・単価・為替・税・安全係数が不足、期限切れ、または不正です。通信前に確認してください。')
    if not key_format:result['missing'].append('APIキーを環境変数に設定し直してください。値は表示・保存しません。')
    return result


def diagnostic_history(service):
    latest={}
    for event in service.store.load()['events']:
        if event['kind']==EVENT:latest[event['diagnosis_id']]=event
    return list(latest.values())


def preview(service,raw,mode='bounded'):
    card=parse_card(raw);price=diagnostic_quote(card)
    if mode not in ('bounded','verified-free'):raise ValueError('診断の利用モードが不正です。')
    if mode=='verified-free':check_free_tier(card)
    evidence=price.execution_evidence(PROVIDER,MODEL,LIMITS)
    data=service.store.load()
    return dict(model=MODEL,mode=mode,maximum_jpy=evidence['computed_max_jpy'],limits=dict(LIMITS),
        approval_key=digest(dict(card=card,mode=mode,revision=data['revision'],body=probe_body(),project_id=service.project_id)),
        revision=data['revision'],body=probe_body())


class Probe:
    def __init__(self,*,send=None):self.send=send

    def generate(self,identifier):
        context=current_execution()
        if not context or context['request_id']!=identifier or context['limits']!=LIMITS or context.get('payload_constraint')!=digest(probe_body()):
            raise ValueError('固定文診断の単発送信権が必要です。')
        body=probe_body();claim_provider_call(PROVIDER,MODEL,payload_sha256=digest(body))
        from manga.story_ai_gemini import transport
        raw=(self.send or transport)(body,api_key())
        try:
            response=parse_response(raw)
            try:
                value=load_json_payload(response['text']);ok=isinstance(value,dict) and set(value)=={'ok'} and value['ok'] is True
            except ValueError:ok=False
            return dict(model=response['model'],usage=response['usage'],status=response['status'],response_valid=ok)
        except Exception:raise GeminiTransportError('invalid_response') from None


def _finish(service,initial,result,*,receipt_sha256=None):
    # 固定のenum以外は履歴へ渡さない。例外/応答本文/URL/ヘッダーは保存しない。
    keep=('diagnosis_id','plan_sha256','ledger_path','mode','maximum_jpy')
    with service.store.lock():
        data=service.store.load()
        # 別画面で正式採用の保存が中断していたら、復旧対象の正本へ追記しない。
        data=service.store.checked(data['revision'])
        service.store.save(data,EVENT,**{k:initial[k] for k in keep},state=result['state'],reason=result['reason'],
                           receipt_sha256=receipt_sha256 or initial.get('receipt_sha256'))
        return next(h for h in diagnostic_history(service) if h['diagnosis_id']==initial['diagnosis_id'])


def run(service,raw,*,mode='bounded',approval=None,expected_revision,probe=None):
    card=parse_card(raw)
    if not service.api_enabled('paid' if mode=='bounded' else 'verified-free'):
        raise ValueError('実通信は既定OFFです。最大費用を承認する診断はMANGA_ENABLE_PAID_STORY、無料枠本人確認済みはMANGA_ENABLE_TEXT_APIを明示的に有効にしてください。')
    api_key()
    with service.store.lock(generation=True):
        plan=preview(service,card,mode)
        if approval is None or approval!=plan['approval_key']:raise ValueError('この固定文・モデル・最大費用の1回限りの承認が必要です。')
        ledger=open_budget(service.directory);ledger_path=str(ledger.path.resolve())
        with service.store.lock():
            data=service.store.checked(expected_revision)
            if data['revision']!=plan['revision']:raise StorageError('診断条件が変更されました。最新の見積を確認してください。')
            paths={j['ledger_path'] for j in data['jobs'] if j['provider']!='mock'}|{h['ledger_path'] for h in diagnostic_history(service)}
            if paths and paths!={ledger_path}:raise ValueError('予算台帳の保存先が履歴と異なります。元の共有台帳を確認してください。')
            if any(h['state'] in ('processing','uncertain') for h in diagnostic_history(service)):
                raise ValueError('前回の診断が処理中・結果不明です。再送せず請求・保存を照合してください。')
            if not BudgetedExecutor(ledger).dry_run(project_id=service.project_id,quote_jpy=plan['maximum_jpy'],provider=PROVIDER)['possible']:
                raise ValueError('作品500円・月間5,000円の残額不足、または請求確認待ちのため送信できません。')
            identifier=str(uuid4())
            initial=dict(diagnosis_id=identifier,plan_sha256=plan['approval_key'],ledger_path=ledger_path,mode=mode,maximum_jpy=plan['maximum_jpy'])
            service.store.save(data,EVENT,**initial,state='processing',reason=None,receipt_sha256=None)
            # 初回メタデータ保存前には成果物を置かない。失敗しても正本欠損扱いにしない。
            service.store.write(f'story_ai/diagnostics/{identifier}.probe',b'probe')
        price=diagnostic_quote(card)
        def operation():
            response=(probe or Probe()).generate(identifier)
            receipt=dict(diagnosis_id=identifier,plan_sha256=initial['plan_sha256'],response=response)
            try:service.store.write(f'story_ai/diagnostics/{identifier}.json',json.dumps(receipt,allow_nan=False).encode())
            except (OSError,StorageError):raise DiagnosticStorageError('応答保存を確認してください。') from None
            return response
        try:
            response=BudgetedExecutor(ledger).run(project_id=service.project_id,request_id=identifier,provider=PROVIDER,model=MODEL,kind='connection-diagnostic',
                price_quote=price,usage_limits=dict(LIMITS),operation=operation,actual_charge_jpy=lambda r:charge_from_usage(price,[MODEL],r))
        except Exception as error:
            reason=error.category if isinstance(error,GeminiTransportError) and error.category in MESSAGES else 'storage_error' if isinstance(error,DiagnosticStorageError) else 'network_error' if isinstance(error,OSError) else 'invalid_response'
            state='unavailable' if reason in ('forbidden','not_found','rate_limited','unauthorized','bad_request') else 'uncertain'
            return _finish(service,initial,dict(state=state,reason=reason))
        payload=service.store.read(f'story_ai/diagnostics/{identifier}.json')
        state='success' if response['status']=='completed' and response['response_valid'] else 'unavailable'
        return _finish(service,initial,dict(state=state,reason=None if state=='success' else 'invalid_response'),receipt_sha256=hashlib.sha256(payload).hexdigest())


def recover(service,identifier):
    validate_id(identifier)
    with service.store.lock(generation=True):
        initial=next((h for h in diagnostic_history(service) if h['diagnosis_id']==identifier),None)
        if not initial:raise ValueError('診断履歴がありません。')
        ledger=open_budget(service.directory)
        if str(ledger.path.resolve())!=initial['ledger_path']:raise ValueError('元の共有予算台帳で照合してください。')
        status=ledger.request_status(identifier)
        if status is None:
            return _finish(service,initial,dict(state='unavailable',reason='not_sent'))
        if status and status['project_id']==service.project_id and status['state']=='cancelled':
            return _finish(service,initial,dict(state='unavailable',reason='interrupted'))
        if not status or status['project_id']!=service.project_id or status['state']!='settled':
            raise ValueError('請求が未確定です。予算画面で明細を照合してから保存を再開してください。APIは再送しません。')
        try:
            content=service.store.read(f'story_ai/diagnostics/{identifier}.json');receipt=json.loads(content)
            if receipt['diagnosis_id']!=identifier or receipt['plan_sha256']!=initial['plan_sha256']:raise ValueError
            if initial.get('receipt_sha256') and hashlib.sha256(content).hexdigest()!=initial['receipt_sha256']:raise ValueError
            response=receipt['response']
            if set(response)!= {'model','usage','status','response_valid'} or response['model']!=MODEL or type(response['response_valid']) is not bool:raise ValueError
        except (ValueError,KeyError,TypeError,StorageError):
            return _finish(service,initial,dict(state='unavailable',reason='invalid_response'))
        state='success' if response['status']=='completed' and response['response_valid'] else 'unavailable'
        if initial['state']==state and initial.get('receipt_sha256')==hashlib.sha256(content).hexdigest():return initial
        return _finish(service,initial,dict(state=state,reason=None if state=='success' else 'invalid_response'),receipt_sha256=hashlib.sha256(content).hexdigest())
