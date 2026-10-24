"""既存音声・動画Storeの検証証跡。画面表示では原寸素材を読み込まない。"""
import json
from pathlib import Path

from manga.storage import StorageError
from manga.ui_data import signature
from manga.voice_models import digest
from manga.voice_service import VoiceService
from manga.video_service import VideoService
from manga.video_models import FORMATS
from manga.video_sources import file_bytes


def media_token(directory, project_id):
    """自分自身の検証イベントを除いた設定と、実ファイルの変更を検知する。"""
    voice = VoiceService(directory, project_id)
    data = voice.store.load()
    video = VideoService(directory, project_id).store.load()
    paths = {a['path'] for a in data['assets']}
    paths.update(p for t in data['timelines'] for p in t['paths'].values())
    paths.update(e['path'] for e in video['exports'])
    paths.update(a['path'] for a in video['audio'])
    art = voice.artwork.load()
    paths.update(i['path'] for e in art['panels'].values() for i in e['candidates'] if i['id'] == e['selected_image_id'])
    paths.update(i['path'] for c in art['characters'] if not c['deleted_at'] for i in c['references'])
    from manga.japanese_text import find_japanese_font
    fonts = {}
    for plans in video['plans'].values():
        for plan in plans.values():
            try:
                font = find_japanese_font(plan['font_path'])
                fonts[str(font.path)] = font.signature
            except (ValueError,OSError,StorageError) as error:
                fonts[plan['font_path']] = dict(error=str(error))
    return digest(dict(story=voice.stories.load_history(), artwork=art, editorial=voice.editorial.load(), voice=data,
        video={k:video[k] for k in ('plans','audio','jobs','exports')}, fonts=fonts,
        files={p:signature(Path(directory)/project_id/p) for p in sorted(paths)}))


def evidence(directory, project_id):
    video = VideoService(directory, project_id).store.load()
    token = media_token(directory, project_id)
    return next((e for e in reversed(video['events']) if e['kind'] == 'production_validation' and e['token'] == token), None)


def validate_media(directory, project_id):
    """利用者の明示操作時だけハッシュ・字幕・実尺・現在の生成元を照合する。"""
    service = VideoService(directory, project_id)
    before = media_token(directory, project_id)
    errors = []
    voice = service.voice
    data = voice.store.load()
    plan = voice.plan(data)
    audio_done = 0
    reviewed = 0
    mock_audio = False
    for row in plan['rows']:
        try:
            if row['needs_review'] or row['asset'] is None:
                raise ValueError(f"{row['page']}ページ{row['panel']}コマの話者・音声が未準備です。")
            asset = row['asset']
            voice.audio_bytes(asset)
            audio_done += 1
            review = data['audio_reviews'].get(asset['id'])
            reviewed += bool(review and review['sha256'] == asset['info']['sha256'])
            mock_audio |= asset['mock'] or asset['settings']['provider'] == 'mock' or asset.get('rights',{}).get('status') == 'test-only'
        except (ValueError, OSError, StorageError) as error:
            errors.append(str(error))
    timeline = None
    source = None
    # 最新の保存版を使う。古い版へ黙って戻さない。
    if data['timelines']:
        record = data['timelines'][-1]
        try:
            from manga.video_sources import snapshot
            from manga.voice_timeline import srt_text
            source = snapshot(voice, record['id'])
            timeline = source['timeline']
            subtitle = file_bytes(service.store.root, record['paths']['srt'], 16*1024*1024)
            if subtitle != srt_text(timeline).encode('utf-8'):
                raise ValueError('保存字幕と脚本・音声タイムラインが一致しません。字幕を保存し直してください。')
        except (ValueError, OSError, StorageError) as error:
            timeline = None
            errors.append(str(error))
    else:
        errors.append('音声タイムライン・SRTを保存してください。')
    video = service.store.load()
    valid = []
    warnings = []
    if timeline:
        for kind in FORMATS:
            try:
                plans = video['plans'].get(timeline['id'])
                if not plans:
                    raise ValueError('3形式の動画編集計画を保存してください。')
                selected_plan = {kind:plans[kind]}
                inspected = service.inspect(timeline['id'], selected_plan)
                warnings.extend(inspected['warnings'])
                from manga.video_jobs import fingerprint
                from manga.video_process import verify_mp4
                from manga.video_models import compile_plan
                # 複数形式ジョブと単形式ジョブの双方を既存の指紋で検証する。
                jobs = {j['id']:j for j in video['jobs'] if j['state'] == 'ready' and j['timeline_id'] == timeline['id']}
                result = next((e for e in reversed(video['exports']) if e['kind'] == kind and e['timeline_id'] == timeline['id']), None)
                if not result or result['job_id'] not in jobs:
                    raise ValueError(kind+'のMP4がありません。')
                job = jobs[result['job_id']]
                if any(video['plans'][timeline['id']][k] != p for k,p in job['plans'].items()):
                    raise ValueError(kind+'の編集計画が変更されています。')
                full_source = service.inspect(timeline['id'], job['plans'])
                if fingerprint(full_source, job['plans']) != result['fingerprint'] or result['fingerprint'] != job['fingerprint'] or result['id'] not in job['export_ids']:
                    raise ValueError(kind+'の生成元が古くなっています。')
                service.export_bytes(result)
                output_plan = plans[kind]
                schedule = compile_plan(output_plan, timeline)
                verify_mp4(service.store.safe(result['path']), FORMATS[kind], output_plan['fps'], schedule['frames']*1000/output_plan['fps'])
                valid.append(result['id'])
            except (ValueError, OSError, StorageError) as error:
                errors.append(str(error))
    if media_token(directory, project_id) != before:
        raise StorageError('検証中に素材・設定が変更されました。保存せず再確認してください。')
    result = dict(token=before, audio_done=audio_done, audio_total=len(plan['rows']), audio_reviewed=reviewed,
        mock_audio=mock_audio, timeline_id=timeline['id'] if timeline else None, export_ids=valid,
        errors=list(dict.fromkeys(errors)), warnings=list(dict.fromkeys(warnings)))
    with service.store.lock():
        current = service.store.load()
        if media_token(directory, project_id) != before:
            raise StorageError('検証後に制作データが変更されました。再検証してください。')
        previous = next((e for e in reversed(current['events']) if e['kind'] == 'production_validation' and all(e.get(k) == v for k,v in result.items())), None)
        if previous:
            return previous
        service.store.save(current, 'production_validation', **result)
        return current['events'][-1]
