"""Googleの構造化400エラーを固定分類へ変換。本文・自由文は返さない。"""
import json

MAX_ERROR_BYTES = 16384
BAD_REQUEST_MESSAGES = {
    'bad_request_api_key': '400：Googleの構造化エラーはAPIキーの無効を示しています。Macのキー設定と発行プロジェクトを確認してください。キーの値は貼り付けないでください。',
    'bad_request_precondition': '400：Googleは実行の前提条件が満たされていないと回答しました。対象プロジェクトの利用条件・地域・請求設定等を確認してください。どの条件かはこの分類だけでは断定できません。',
    'bad_request_json_schema': '400：Googleの検証詳細がJSONスキーマの指定を指しています。アプリの版と構造化出力の対応を確認してください。スキーマを自動変更して再送しません。',
    'bad_request_thinking': '400：Googleの検証詳細が思考設定を指しています。対象モデルのthinkingConfig・thinkingBudgetの対応を確認してください。モデルや予算を自動変更しません。',
    'bad_request_mime_type': '400：Googleの検証詳細が応答形式の指定を指しています。responseMimeTypeとスキーマの組合せを確認してください。',
    'bad_request_output_limit': '400：Googleの検証詳細が出力上限を指しています。モデルの上限とmaxOutputTokensを確認してください。費用上限を自動で増やしません。',
    'bad_request_contents': '400：Googleの検証詳細が送信内容の構造を指しています。contents・role・partsのAPI仕様とアプリの版を確認してください。',
    'bad_request_generation_config': '400：Googleの検証詳細が生成設定を指していますが、個別の項目は特定できません。generationConfigのAPI仕様とアプリの版を確認してください。',
    'bad_request_multiple_fields': '400：Googleの検証詳細が複数の生成設定を指しています。API仕様とアプリの版を確認してください。条件を自動変更して再送しません。',
    'bad_request_invalid_argument': '400：Googleは引数が不正と回答しましたが、安全に分類できる対象項目はありません。公式仕様・アプリの版・プロジェクト設定を確認してください。原因は断定しません。',
}

_FIELDS = {
    'generationConfig.responseJsonSchema': 'bad_request_json_schema',
    'generationConfig.responseSchema': 'bad_request_json_schema',
    'generationConfig.thinkingConfig': 'bad_request_thinking',
    'generationConfig.responseMimeType': 'bad_request_mime_type',
    'generationConfig.maxOutputTokens': 'bad_request_output_limit',
    'contents': 'bad_request_contents',
    'generationConfig': 'bad_request_generation_config',
}
_ALIASES = {'generation_config':'generationConfig', 'response_json_schema':'responseJsonSchema',
            'response_schema':'responseSchema', 'thinking_config':'thinkingConfig',
            'response_mime_type':'responseMimeType', 'max_output_tokens':'maxOutputTokens'}


def classify_bad_request(raw):
    """公式google.rpc.ErrorInfo/BadRequestとstatusの固定値のみ。message等は参照しない。"""
    if not isinstance(raw, bytes) or len(raw)>MAX_ERROR_BYTES:
        return 'bad_request'
    try:
        error=json.loads(raw.decode('utf-8'))['error']
        if not isinstance(error,dict) or type(error.get('code')) is not int or error['code']!=400:
            return 'bad_request'
        status=error.get('status')
        if status=='FAILED_PRECONDITION':return 'bad_request_precondition'
        if status!='INVALID_ARGUMENT':return 'bad_request'
        details=error.get('details',[])
        if not isinstance(details,list):return 'bad_request_invalid_argument'
        matches=set()
        for detail in details[:64]:
            if not isinstance(detail,dict):continue
            if (detail.get('@type')=='type.googleapis.com/google.rpc.ErrorInfo'
                    and detail.get('domain')=='googleapis.com' and detail.get('reason')=='API_KEY_INVALID'):
                return 'bad_request_api_key'
            if detail.get('@type')!='type.googleapis.com/google.rpc.BadRequest':continue
            violations=detail.get('fieldViolations',[])
            if not isinstance(violations,list):continue
            for item in violations[:64]:
                field=item.get('field') if isinstance(item,dict) else None
                if not isinstance(field,str) or len(field)>512:continue
                field='.'.join(_ALIASES.get(part,part) for part in field.split('.'))
                for prefix,category in _FIELDS.items():
                    if field==prefix or field.startswith(prefix+'.') or field.startswith(prefix+'['):
                        matches.add(category)
                        break
        return next(iter(matches)) if len(matches)==1 else 'bad_request_multiple_fields' if matches else 'bad_request_invalid_argument'
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        return 'bad_request'


def read_bad_request(error):
    """既に受け取ったHTTP 400を上限付きで読む。新しいHTTP送信は行わない。"""
    try:
        return classify_bad_request(error.read(MAX_ERROR_BYTES+1))
    except Exception:
        return 'bad_request'
    finally:
        try:error.close()
        except Exception:pass
