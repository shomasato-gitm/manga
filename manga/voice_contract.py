"""FFmpeg工程が音声タイムラインを読むための依存なしバリデーター。"""
from manga.models import validate_id
from manga.voice_models import voice_settings,speech_text,EMOTIONS
import re


def validate_timeline(value):
    try:
        if type(value['schema_version']) is not int or value['schema_version']!=1 or value['format']!='manga-voice-timeline':raise ValueError
        for key in ('id','project_id','story_version_id'):validate_id(value[key])
        if value['timeline_version_id']!=value['id']:raise ValueError
        if type(value['duration_ms']) is not int or value['duration_ms']<=0:raise ValueError
        if type(value['needs_review']) is not bool or not isinstance(value['warnings'],list):raise ValueError
        if value['needs_review']!=bool(value['warnings']):raise ValueError
        if not isinstance(value['scenes'],list) or not 1<=len(value['scenes'])<=600:raise ValueError
        if not isinstance(value['assets'],list) or not 1<=len(value['assets'])<=10000:raise ValueError
        assets={}
        for asset in value['assets']:
            identifier=validate_id(asset['id'])
            if identifier in assets or asset['path']!=f'voice/audio/{identifier}.wav':raise ValueError
            info=asset['info']
            if type(info['sample_rate']) is not int or info['sample_rate']!=16000 or type(info['channels']) is not int or info['channels']!=1 or type(info['frames']) is not int or not 0<info['frames']<=16000*600:raise ValueError
            if type(info['duration_ms']) is not int or info['duration_ms']!=(info['frames']*1000+15999)//16000 or not re.fullmatch(r'[0-9a-f]{64}',info['sha256']):raise ValueError
            validate_id(asset['speaker_id'])
            if voice_settings(asset['settings'])!=asset['settings']:raise ValueError
            assets[identifier]=asset
        utterances=[];ids=set();previous_end=0;scene_ids=set();last_number=(0,0)
        for scene in value['scenes']:
            if scene['id'] in scene_ids or scene['id']!=f"p{scene['page']}-c{scene['panel']}":raise ValueError
            scene_ids.add(scene['id'])
            if type(scene['page']) is not int or not 1<=scene['page']<=100 or type(scene['panel']) is not int or not 1<=scene['panel']<=6:raise ValueError
            if (scene['page'],scene['panel'])<=last_number:raise ValueError
            last_number=(scene['page'],scene['panel'])
            image=scene['image']
            if image is not None:
                validate_id(image['id'])
                if image['path']!=f"images/panels/{image['id']}.png" or not re.fullmatch(r'[0-9a-f]{64}',image['sha256']):raise ValueError
            start,end=scene['start_ms'],scene['end_ms']
            if type(start) is not int or type(end) is not int or not previous_end<=start<end<=value['duration_ms']:raise ValueError
            position=start
            for line in scene['utterances']:
                validate_id(line['id']);validate_id(line['speaker_id'])
                if line['id'] in ids or line['asset_id'] not in assets:raise ValueError
                ids.add(line['id'])
                if not isinstance(line['text'],str) or not line['text'].strip():raise ValueError
                speech_text(line['spoken_text'])
                if line['kind'] not in ('dialogue','narration') or line['emotion'] not in EMOTIONS or line['speaker_id']!=assets[line['asset_id']]['speaker_id']:raise ValueError
                if type(line['start_ms']) is not int or type(line['end_ms']) is not int:raise ValueError
                if not position<=line['start_ms']<line['end_ms']<=end:raise ValueError
                if line['end_ms']-line['start_ms']!=assets[line['asset_id']]['info']['duration_ms']:raise ValueError
                utterances.append(dict(utterance_id=line['id'],text=line['text'],start_ms=line['start_ms'],end_ms=line['end_ms']))
                position=line['end_ms']
            previous_end=end
        if previous_end!=value['duration_ms'] or utterances!=value['subtitles']:raise ValueError
        return value
    except (KeyError,TypeError,AttributeError,ValueError,IndexError) as error:
        raise ValueError('音声タイムラインの版・ID・保存先・実尺・字幕対応が不正です。') from error
