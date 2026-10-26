"""Structured Outputs用JSON Schema。受信後も既存の検証器を通す。"""
from manga.editorial_models import CRITERIA
from manga.story_ai_models import IDEA_FIELDS


def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def string():return dict(type='string')
def array(items):return dict(type='array',items=items)
def integer():return dict(type='integer')


def validate_structure(value,rule):
    """送信に使う同じ小さなスキーマを受信後にも検証する。手動取り込みの互換性は維持。"""
    import math
    kind=rule['type'];kinds=kind if isinstance(kind,list) else [kind]
    finite_number=type(value) is int or type(value) is float and math.isfinite(value)
    matches={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),
             'integer':type(value) is int,'number':finite_number,'null':value is None}
    if not any(matches[k] for k in kinds) or 'enum' in rule and value not in rule['enum']:
        raise ValueError('AI回答の必須項目・型・対象IDがスキーマと一致しません。保存応答を取得し、手動JSON取り込みで修正・確認してください。')
    if isinstance(value,dict):
        if set(value)!=set(rule['properties']):raise ValueError('AI回答の必須項目に不足・余分な項目があります。自動修正や正式採用は行いません。')
        for k,v in value.items():validate_structure(v,rule['properties'][k])
    if isinstance(value,list):
        for v in value:validate_structure(v,rule['items'])


def schema(kind,candidate_id=None):
    if kind=='concepts':return obj(dict(ideas=array(obj({k:string() for k in IDEA_FIELDS}))))
    if kind=='evaluation':
        target=obj(dict(page=integer(),panel={'type':['integer','null']}))
        item=obj(dict(score=dict(type='number'),good_points=string(),problems=string(),reason=string(),targets=array(target),suggestion=string()))
        return obj(dict(schema_version=dict(type='integer',enum=[1]),story_version_id=dict(type='string',enum=[candidate_id]),
            criteria=obj({k:item for k in CRITERIA}),summary=string()))
    panel=obj(dict(panel=integer(),description=string(),dialogue=string(),narration=string()))
    page=obj(dict(page=integer(),summary=string(),panels=array(panel)))
    story=obj(dict(schema_version=dict(type='integer',enum=[1]),title=string(),summary=string(),pages=array(page)))
    return obj(dict(story=story,page_hooks=array(obj(dict(page=integer(),hook=string()))),change_reason=string()))
