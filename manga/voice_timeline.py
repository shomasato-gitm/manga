"""WAV実尺から字幕とFFmpeg用の独立タイムラインを生成する。動画は作らない。"""
import copy
import hashlib
import html
import json
from uuid import uuid4

from manga.models import now_iso
from manga.artwork_models import image_context_changed
from manga.storage import StorageError
from manga.voice_audio import inspect_wav
from manga.voice_models import digest


def srt_timestamp(milliseconds):
    hours,rest=divmod(milliseconds,3600000)
    minutes,rest=divmod(rest,60000)
    seconds,millis=divmod(rest,1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{millis:03}'


def srt_text(timeline):
    from manga.voice_contract import validate_timeline
    validate_timeline(timeline)
    lines=[]
    for index,row in enumerate(timeline['subtitles'],1):
        text=html.escape(row['text']).replace('\r','').strip()
        lines.append(f"{index}\n{srt_timestamp(row['start_ms'])} --> {srt_timestamp(row['end_ms'])}\n{text}\n")
    return '\n'.join(lines)


def build_timeline(service,*,gap_ms=200,silent_panel_ms=1500):
    if type(gap_ms) is not int or not 0<=gap_ms<=5000 or type(silent_panel_ms) is not int or not 100<=silent_panel_ms<=60000:
        raise ValueError('間隔は0〜5000ms、無音コマは100〜60000msで指定してください。')
    data=service.store.load();plan=service.plan(data)
    if not plan['rows']:raise ValueError('発話・ナレーションがありません。')
    assets={};by_panel={};subtitles=[];cursor=0;scenes=[];warnings=[]
    if not plan['confirmed']:warnings.append('現在の脚本版は未確定です。')
    for row in plan['rows']:
        if row['needs_review'] or not row['asset']:
            raise ValueError(f"{row['page']}ページ{row['panel']}コマの話者・音声を準備してください。")
        asset=row['asset'];content=service.audio_bytes(asset);info=inspect_wav(content)
        asset=copy.deepcopy(asset);asset['info'].update(info)
        review=data['audio_reviews'].get(asset['id'])
        asset['human_reviewed']=bool(review and review['sha256']==info['sha256'])
        if asset['mock']:warnings.append('確認用トーンを含みます。日本語音声の完成品ではありません。')
        if not asset['human_reviewed']:warnings.append('人間が未確認の音声があります。')
        assets[asset['id']]=asset
        by_panel.setdefault((row['page'],row['panel']),[]).append((row,asset))
    for page in plan['version']['story']['pages']:
        for panel in page['panels']:
            number=(page['page'],panel['panel']);start=cursor;utterances=[]
            pairs=by_panel.get(number,[])
            for index,(row,asset) in enumerate(pairs):
                end=cursor+asset['info']['duration_ms']
                utterance=dict(id=row['id'],speaker_id=row['speaker_id'],kind=row['kind'],text=row['text'],
                    spoken_text=row['spoken_text'],asset_id=asset['id'],emotion=row['settings']['emotion'],start_ms=cursor,end_ms=end)
                utterances.append(utterance)
                subtitles.append(dict(utterance_id=row['id'],text=row['text'],start_ms=cursor,end_ms=end))
                cursor=end+(gap_ms if index+1<len(pairs) else 0)
            if not pairs:cursor+=silent_panel_ms
            image=None
            entry=plan['artwork']['panels'].get(f'{number[0]}:{number[1]}',{})
            selected=next((i for i in entry.get('candidates',[]) if i['id']==entry.get('selected_image_id')),None)
            if selected:
                if selected['original_name'].startswith('image-ai-') and selected['original_name'].endswith('-TEST_ONLY.png'):
                    warnings.append('AI生成ではないテスト用コマ画像を含みます。公開用の完成画像品質は未確認です。')
                try:
                    image=dict(id=selected['id'],path=selected['path'],sha256=hashlib.sha256(service.artwork.image_bytes(selected)).hexdigest())
                    image['story_version_id']=selected['story_version_id']
                    if selected['source_panel']!=panel or image_context_changed(selected,entry,plan['artwork']):
                        warnings.append(f'{number[0]}ページ{number[1]}コマの画像と現在の作画設定は要確認です。')
                except (OSError,ValueError,StorageError):warnings.append(f'{number[0]}ページ{number[1]}コマの画像が欠損しています。')
            if image is None:warnings.append('採用画像がないコマがあります。動画生成前に画像を準備してください。')
            scenes.append(dict(id=f'p{number[0]}-c{number[1]}',page=number[0],panel=number[1],description=panel['description'],
                start_ms=start,end_ms=cursor,image=image,utterances=utterances,silent_duration_user_setting=not bool(pairs)))
            cursor+=gap_ms
    source=dict(plan_token=plan['token'],artwork_revision=plan['artwork']['revision'],
                assets=[dict(id=a['id'],sha256=a['info']['sha256'],human_reviewed=a['human_reviewed']) for a in assets.values()],
                images=[s['image'] for s in scenes],gap_ms=gap_ms,silent_panel_ms=silent_panel_ms)
    result=dict(schema_version=1,format='manga-voice-timeline',project_id=service.project_id,id=str(uuid4()),created_at=now_iso(),
        story_version_id=plan['version']['id'],story_sha256=digest(plan['version']['story']),settings_revision=data['settings_revision'],
        artwork_revision=plan['artwork']['revision'],source_fingerprint=digest(source),duration_ms=scenes[-1]['end_ms'],
        options=dict(gap_ms=gap_ms,silent_panel_ms=silent_panel_ms),assets=list(assets.values()),scenes=scenes,
        subtitles=subtitles,needs_review=bool(warnings),warnings=list(dict.fromkeys(warnings)),publication_review_required=True,
        billing_note='料金は共有台帳で管理。音声単価の円換算額は最終請求書を取得したものではない。')
    from manga.voice_contract import validate_timeline
    result['timeline_version_id']=result['id']
    return validate_timeline(result)


def save_timeline(service,*,expected_revision,gap_ms=200,silent_panel_ms=1500):
    with service.store.locked(sources=True):
        data=service.store.checked(expected_revision)
        timeline=build_timeline(service,gap_ms=gap_ms,silent_panel_ms=silent_panel_ms)
        identifier=timeline['id']
        paths=dict(json=f'voice/exports/{identifier}.json',srt=f'voice/exports/{identifier}.srt')
        service.store.write_blob(paths['json'],json.dumps(timeline,ensure_ascii=False,indent=2).encode())
        service.store.write_blob(paths['srt'],srt_text(timeline).encode('utf-8'))
        record=dict(id=identifier,created_at=timeline['created_at'],paths=paths,source_fingerprint=timeline['source_fingerprint'],
                    story_version_id=timeline['story_version_id'],options=timeline['options'],duration_ms=timeline['duration_ms'])
        data['timelines'].append(record);service.store.save(data,'timeline',timeline_id=identifier)
        return timeline


def timeline_is_current(service,record):
    try:return build_timeline(service,**record['options'])['source_fingerprint']==record['source_fingerprint']
    except (ValueError,OSError,StorageError):return False
