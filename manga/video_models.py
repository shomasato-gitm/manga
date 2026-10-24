"""動画専用の編集計画。既存の音声タイムラインは変更しない。"""
import copy
import math
from uuid import uuid4

from manga.models import validate_id

FORMATS = {'youtube_main': (1920,1080), 'tiktok_vertical': (1080,1920), 'youtube_shorts': (1080,1920)}
MOTIONS = ('still','zoom_in','zoom_out','pan_left','pan_right')


def number(value,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('数値の範囲が不正です。')
    return value


def default_plans(timeline):
    result={}
    scenes=timeline['scenes']
    for kind in FORMATS:
        order=list(scenes)
        if kind=='youtube_shorts':
            hook=max(scenes,key=lambda s:len(s['utterances']))
            order=[hook]+[s for s in scenes if s['id']!=hook['id']]
        if kind!='youtube_main':
            picked=[];duration=0
            for scene in order:
                span=scene['end_ms']-scene['start_ms']
                if duration+span+200>75000:continue
                picked.append(scene);duration+=span+200
                if duration>=55000:break
            order=picked or [order[0]]
        rows=[]
        for i,scene in enumerate(order):
            gap=(scenes[i+1]['start_ms']-scene['end_ms']) if kind=='youtube_main' and i+1<len(scenes) else 200
            rows.append(dict(id=str(uuid4()),scene_id=scene['id'],utterance_id=None,motion=MOTIONS[1+i%4],
                framing='fit',focus=[0.0,0.0,1.0,1.0],gap_after_ms=gap if i+1<len(order) else 0,transition='fade'))
        result[kind]=dict(schema_version=1,format='manga-video-plan',kind=kind,timeline_id=timeline['id'],
            rows=rows,fps=24,quality='balanced',subtitle_position='bottom',font_path='',hook='',
            rights_reviewed=False,editor_reviewed=False,bgm=None,sfx=[])
    return result


def validate_plan(plan,timeline):
    try:
        if type(plan['schema_version']) is not int or plan['schema_version']!=1 or plan['format']!='manga-video-plan':raise ValueError
        if plan['kind'] not in FORMATS or plan['timeline_id']!=timeline['id']:raise ValueError
        if type(plan['fps']) is not int or plan['fps'] not in (12,24,30) or plan['quality'] not in ('fast','balanced'):raise ValueError
        if plan['subtitle_position'] not in ('bottom','top') or not isinstance(plan['font_path'],str) or len(plan['font_path'])>1024:raise ValueError
        if not isinstance(plan['hook'],str) or len(plan['hook'])>120:raise ValueError
        if type(plan['rights_reviewed']) is not bool or type(plan['editor_reviewed']) is not bool:raise ValueError
        if not isinstance(plan['rows'],list) or not 1<=len(plan['rows'])<=600:raise ValueError
        scenes={s['id']:s for s in timeline['scenes']};ids=set()
        for row in plan['rows']:
            validate_id(row['id'])
            if row['id'] in ids or row['scene_id'] not in scenes:raise ValueError
            ids.add(row['id'])
            scene=scenes[row['scene_id']]
            if row['utterance_id'] is not None and row['utterance_id'] not in {u['id'] for u in scene['utterances']}:raise ValueError
            if row['motion'] not in MOTIONS or row['framing'] not in ('fit','focus') or row['transition'] not in ('cut','fade'):raise ValueError
            focus=row['focus']
            if not isinstance(focus,list) or len(focus)!=4:raise ValueError
            x,y,w,h=[number(v,0,1) for v in focus]
            if w<=0 or h<=0 or x+w>1.000001 or y+h>1.000001:raise ValueError
            if type(row['gap_after_ms']) is not int or not 0<=row['gap_after_ms']<=5000:raise ValueError
        if plan['kind']=='youtube_main':
            if [r['scene_id'] for r in plan['rows']]!=[s['id'] for s in timeline['scenes']]:raise ValueError
            for i,row in enumerate(plan['rows']):
                gap=timeline['scenes'][i+1]['start_ms']-timeline['scenes'][i]['end_ms'] if i+1<len(plan['rows']) else 0
                if row['utterance_id'] is not None or row['gap_after_ms']!=gap:raise ValueError
        if not isinstance(plan['sfx'],list) or len(plan['sfx'])>20:raise ValueError
        for audio in ([plan['bgm']] if plan['bgm'] else [])+plan['sfx']:
            validate_id(audio['id']);number(audio['volume'],0,1)
            if type(audio['start_ms']) is not int or not 0<=audio['start_ms']<=600000:raise ValueError
            if type(audio['fade_ms']) is not int or not 0<=audio['fade_ms']<=5000:raise ValueError
        schedule=compile_plan(plan,timeline)
        if not 100<=schedule['duration_ms']<=600000:raise ValueError
        return plan
    except (KeyError,TypeError,ValueError,AttributeError) as error:
        raise ValueError('動画計画の版・形式・コマ／発話・注目領域・時間・設定を確認してください。本編は全コマの順序と尺を維持します。') from error


def compile_plan(plan,timeline):
    scenes={s['id']:s for s in timeline['scenes']};cursor=0;rows=[];subtitles=[]
    fps=plan['fps']
    for row in plan['rows']:
        scene=scenes[row['scene_id']]
        speech=scene['utterances'] if row['utterance_id'] is None else [u for u in scene['utterances'] if u['id']==row['utterance_id']]
        start=scene['start_ms'] if row['utterance_id'] is None else speech[0]['start_ms']
        end=scene['end_ms'] if row['utterance_id'] is None else speech[0]['end_ms']
        units=[]
        for u in speech:
            item=copy.deepcopy(u)
            item.update(start_ms=cursor+u['start_ms']-start,end_ms=cursor+u['end_ms']-start)
            units.append(item);subtitles.append(dict(text=u['text'],start_ms=item['start_ms'],end_ms=item['end_ms'],utterance_id=u['id']))
        stop=cursor+end-start+row['gap_after_ms']
        first=(cursor*fps+999)//1000;last=(stop*fps+999)//1000
        if last<=first:raise ValueError('1フレームより短いシーンは出力できません。')
        rows.append(dict(edit=copy.deepcopy(row),source_scene=scene,start_ms=cursor,end_ms=stop,
            start_frame=first,frames=last-first,utterances=units,source_start_ms=start,source_end_ms=end))
        cursor=stop
    return dict(rows=rows,subtitles=subtitles,duration_ms=cursor,frames=(cursor*fps+999)//1000)
