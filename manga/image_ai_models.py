"""正規Artworkと独立した画像計画・候補・レビューの検証。"""
import re
from datetime import datetime

from manga.artwork_models import panel_key, text
from manga.models import validate_id

STATES = ('queued', 'running', 'ready', 'artifact_pending', 'cancelled')
REVIEW_FIELDS = {'identity':'人物の顔・固定特徴', 'clothing':'衣装・場面例外', 'anatomy':'人体・手指', 'framing':'構図・見切れ・余白', 'no_text':'不要な文字・吹き出し', 'rights':'素材の利用権と出典'}
NOTICE = '設定・版・ID・ハッシュの静的管理です。画像の人物一致や完成品質は人間の目視確認が必要です。'
SIZES = {'1024x1024': (1024,1024), '1536x1024': (1536,1024), '1024x1536': (1024,1536)}


def empty(project_id):
    return dict(schema_version=1, format='manga-image-ai', project_id=project_id, revision=0,
                jobs=[], candidates=[], reviews=[], adoptions=[], events=[])


def sha(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value): raise ValueError('画像ハッシュが不正です。')
    return value


def settings(raw=None):
    raw = raw or {}
    if not isinstance(raw, dict) or not set(raw) <= {'size','composition_note','focus','margin'}: raise ValueError('画像設定の項目が不正です。')
    size = raw.get('size','1024x1024')
    if size not in SIZES: raise ValueError('画像サイズを選択してください。')
    focus = raw.get('focus',dict(x=0.5,y=0.5,width=0.6,height=0.7))
    if not isinstance(focus, dict) or set(focus) != {'x','y','width','height'} or any(type(v) not in (int,float) or not 0<=v<=1 for v in focus.values()) or not focus['width'] or not focus['height']:
        raise ValueError('フォーカスは0〜1の正規化座標で指定してください。')
    if focus['x']-focus['width']/2 < -1e-9 or focus['x']+focus['width']/2 > 1+1e-9 or focus['y']-focus['height']/2 < -1e-9 or focus['y']+focus['height']/2 > 1+1e-9:
        raise ValueError('フォーカス領域が画像外に出ています。')
    return dict(size=size, composition_note=text(raw.get('composition_note',''), '構図補足', 2000), focus=focus,
                margin=text(raw.get('margin','顔・手・重要物を外縁から離し、字幕用の余白を確保'), '余白', 1000))


def validate(data, project_id):
    if not isinstance(data,dict) or set(data) != set(empty(project_id)) or data['schema_version'] != 1 or type(data['schema_version']) is not int or data['format'] != 'manga-image-ai' or data['project_id'] != project_id:
        raise ValueError('画像制作JSONの版・作品ID・形式が不正です。')
    if type(data['revision']) is not int or data['revision']<0: raise ValueError
    seen = set()
    for key in ('jobs','candidates','reviews','adoptions','events'):
        if not isinstance(data[key],list): raise ValueError
        for row in data[key]:
            validate_id(row['id'])
            if row['id'] in seen or datetime.fromisoformat(row['saved_at']).tzinfo is None: raise ValueError('画像制作履歴のID・日時が不正です。')
            seen.add(row['id'])
    jobs = {j['id']:j for j in data['jobs']}
    for j in jobs.values():
        if j['request_id']!=j['id'] or j['state'] not in STATES or j['provider'] not in ('mock','manual') or j['api_calls']!=0 or j['cost_jpy']!='0': raise ValueError
        validate_id(j['candidate_id']); plan=j['plan']
        validate_id(plan['story_version_id']); sha(plan['material_token']); sha(plan['prompt_sha256']); sha(j['job_key'])
        if settings(plan['options']) != plan['options']: raise ValueError
        if plan['kind']=='character': validate_id(plan['character_id'])
        elif plan['kind']=='panel': panel_key(plan['page'],plan['panel'])
        else: raise ValueError
        text(plan['prompt'],'画像プロンプト',200000,True)
        for ref in plan['reference_images']:
            validate_id(ref['id']); validate_id(ref['character_id']); sha(ref['sha256'])
            if ref['path']!=f"images/references/{ref['id']}.png": raise ValueError
    candidates={c['id']:c for c in data['candidates']}
    for c in candidates.values():
        if c['job_id'] not in jobs or c['id'] != jobs[c['job_id']]['candidate_id'] or c['path']!=f"image_ai/candidates/{c['id']}.png": raise ValueError('候補画像のID・保存先が不正です。')
        sha(c['sha256'])
        if type(c['width']) is not int or type(c['height']) is not int or not 0<c['width']*c['height']<=24000000 or min(c['width'],c['height'])<1: raise ValueError
        if c['provider']!=jobs[c['job_id']]['provider'] or c['mock']!=(c['provider']=='mock'): raise ValueError
    for r in data['reviews']:
        if r['candidate_id'] not in candidates or r['checks']!={k:True for k in REVIEW_FIELDS}: raise ValueError('人間の品質確認が不足しています。')
        sha(r['sha256']); sha(r['material_token']); text(r['note'],'人間の確認メモ',2000,True)
    for a in data['adoptions']:
        if a['candidate_id'] not in candidates or a['review_id'] not in {r['id'] for r in data['reviews']}: raise ValueError
        validate_id(a['image_id']); sha(a['registered_sha256'])
    return data
