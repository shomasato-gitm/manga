"""企画と候補脚本の独立形式。正式なstory/schema_version:1は変更しない。"""
import copy
import unicodedata

from manga.editorial_models import text,normalize_evaluation
from manga.story import normalize_story

IDEA_FIELDS=('title','logline','synopsis','protagonist_goal','characters','conflict','turning_point','ending','originality','risks')


def settings(raw):
    result={k:text(raw.get(k,''),label,limit,True) for k,label,limit in
        (('genre','ジャンル',50),('theme','テーマ',2000),('mood','雰囲気',300))}
    for key,low,high,default in (('page_count',1,100,8),('concept_count',3,5,3),('max_cycles',0,5,3)):
        value=raw.get(key,default)
        if type(value) is not int or not low<=value<=high:raise ValueError(f'{key}は{low}〜{high}の整数にしてください。')
        result[key]=value
    goal=raw.get('goal_score',85)
    if type(goal) not in (int,float) or not 0<=goal<=100:raise ValueError('目標点は0〜100にしてください。')
    result['goal_score']=goal
    stop=raw.get('stop_on_regression',True)
    if type(stop) is not bool:raise ValueError('品質低下時の停止設定が不正です。')
    result['stop_on_regression']=stop
    refs=raw.get('reference_ids',[])
    from manga.models import validate_id
    if not isinstance(refs,list) or len(refs)>3 or len(set(refs))!=len(refs):raise ValueError('参考作品は重複なく3件までです。')
    for identifier in refs:validate_id(identifier)
    result['reference_ids']=list(refs)
    return result


def normalize_result(kind,payload,request):
    if not isinstance(payload,dict):raise ValueError('AIの回答はJSONオブジェクトが必要です。')
    config=request['settings']
    if kind=='concepts':
        if set(payload)!={'ideas'} or not isinstance(payload['ideas'],list) or len(payload['ideas'])!=config['concept_count']:raise ValueError('設定した件数の企画案が必要です。')
        ideas=[]
        for row in payload['ideas']:
            if not isinstance(row,dict) or set(row)!=set(IDEA_FIELDS):raise ValueError('企画案の必須項目を確認してください。')
            ideas.append({k:text(row[k],k,100 if k=='title' else 2000,True) for k in IDEA_FIELDS})
        canonical=lambda s:unicodedata.normalize('NFKC',s).casefold().strip()
        if len({canonical(i['title']) for i in ideas})!=len(ideas) or len({(canonical(i['conflict']),canonical(i['ending'])) for i in ideas})!=len(ideas):raise ValueError('同じタイトル、または葛藤と結末が重複する企画案があります。')
        return dict(ideas=ideas)
    if kind=='evaluation':
        return normalize_evaluation(payload,request['candidate']['id'],request['candidate']['story'])
    if set(payload)!={'story','page_hooks','change_reason'}:raise ValueError('脚本回答にはstory・page_hooks・change_reasonが必要です。')
    normalized=normalize_story(payload['story'],config['page_count'])
    hooks=payload['page_hooks']
    if not isinstance(hooks,list) or len(hooks)!=config['page_count']:raise ValueError('全ページの引きを記録してください。')
    for index,row in enumerate(hooks,1):
        if not isinstance(row,dict) or set(row)!={'page','hook'} or type(row['page']) is not int or row['page']!=index:raise ValueError('ページの引きは重複・欠番なしの連番にしてください。')
        text(row['hook'],'ページの引き',1000,True)
    return dict(story=normalized.story,page_hooks=copy.deepcopy(hooks),change_reason=text(payload['change_reason'],'変更理由',2000,True),warnings=normalized.warnings)


def regressed(before,after):
    return after['total_score']<before['total_score'] or any(after['criteria'][k]['score']<before['criteria'][k]['score'] for k in before['criteria'])
