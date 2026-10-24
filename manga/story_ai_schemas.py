"""Structured Outputs用JSON Schema。受信後も既存の検証器を通す。"""
from manga.editorial_models import CRITERIA
from manga.story_ai_models import IDEA_FIELDS


def obj(properties):return dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
def string():return dict(type='string')
def array(items):return dict(type='array',items=items)
def integer():return dict(type='integer')


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
