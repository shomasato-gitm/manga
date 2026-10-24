"""漫画・音声は読み取り、動画の計画・素材・履歴だけを独立保存する。"""
import copy
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from manga.models import validate_id,now_iso
from manga.storage import StorageError
from manga.voice_models import digest
from manga.voice_service import VoiceService
from manga.voice_contract import validate_timeline
from manga.japanese_text import find_japanese_font
from manga.video_storage import VideoStore
from manga.video_models import default_plans,validate_plan,compile_plan,FORMATS
from manga.video_sources import snapshot,file_bytes
from manga.video_process import executables,probe,VideoError
from manga.video_visuals import canvas,subtitle_image,png


class VideoService:
    def __init__(self,directory,project_id):
        self.directory=directory;self.project_id=validate_id(project_id)
        self.store=VideoStore(directory,project_id);self.voice=VoiceService(directory,project_id)

    def timeline(self,identifier):
        validate_id(identifier)
        raw=file_bytes(self.store.root,f'voice/exports/{identifier}.json',16*1024*1024)
        result=validate_timeline(json.loads(raw))
        if result['project_id']!=self.project_id or result['id']!=identifier:raise ValueError('音声タイムラインの作品・版が一致しません。')
        return result

    def plans(self,timeline):
        data=self.store.load()
        return copy.deepcopy(data['plans'].get(timeline['id']) or default_plans(timeline))

    def save_plans(self,timeline_id,plans,*,expected_revision):
        timeline=self.timeline(timeline_id)
        if set(plans)!=set(FORMATS):raise ValueError('3形式の独立した編集計画を保存してください。')
        for kind,plan in plans.items():
            validate_plan(plan,timeline)
            if kind!=plan['kind']:raise ValueError('保存する形式と計画が異なります。')
        with self.store.lock():
            data=self.store.checked(expected_revision)
            if any(j['state'] in ('queued','running') for j in data['jobs']):raise StorageError('動画処理中は編集計画を変更できません。完了・中断を確認してください。')
            before=copy.deepcopy(data['plans'].get(timeline_id))
            data['plan_history'].append(dict(id=str(uuid4()),saved_at=now_iso(),timeline_id=timeline_id,before=before,plans=copy.deepcopy(plans)))
            data['plans'][timeline_id]=copy.deepcopy(plans)
            self.store.save(data,'plans',timeline_id=timeline_id)

    def restore_plans(self,history_id,*,expected_revision):
        record=next((r for r in self.store.load()['plan_history'] if r['id']==history_id),None)
        if not record:raise ValueError('復元する動画計画がありません。')
        self.save_plans(record['timeline_id'],record['plans'],expected_revision=expected_revision)

    def inspect(self,timeline_id,plans,*,locked=False):
        executables()
        with (nullcontext() if locked else self.store.source_lock()):source=snapshot(self.voice,timeline_id)
        warnings=list(source['warnings'])
        for kind,plan in plans.items():
            validate_plan(plan,source['timeline'])
            if kind!=plan['kind']:raise ValueError('出力形式が一致しません。')
            font=find_japanese_font(plan['font_path'])
            for row in compile_plan(plan,source['timeline'])['rows']:
                for u in row['utterances']:
                    try:subtitle_image(u['text'],FORMATS[kind],font,plan['subtitle_position'])
                    except ValueError as error:
                        scene=row['source_scene'];raise ValueError(f"{scene['page']}ページ{scene['panel']}コマの字幕：{error}") from error
            if plan['hook']:subtitle_image(plan['hook'],FORMATS[kind],font,'top')
            duration=compile_plan(plan,source['timeline'])['duration_ms']
            low,high=(180000,300000) if kind=='youtube_main' else (45000,75000)
            if not low<=duration<=high:warnings.append(f'{kind}：実尺{duration/1000:.2f}秒。目標{low/1000:.0f}〜{high/1000:.0f}秒を満たしません。水増し・発話途中の切断はしません。')
            if not plan['rights_reviewed']:warnings.append(f'{kind}：画像・音声等の利用権を人間が確認してください。')
            if not plan['editor_reviewed']:warnings.append(f'{kind}：縦型の意味・重要人物・読み順を含む編集計画は未確認です。')
        extras={a['id']:a for a in self.store.load()['audio']}
        for plan in plans.values():
            for item in ([plan['bgm']] if plan['bgm'] else [])+plan['sfx']:
                asset=extras.get(item['id'])
                if not asset or asset['rights_reviewed'] is not True:raise ValueError('BGM/SFXの利用権を確認して登録してください。')
                payload=file_bytes(self.store.root,asset['path'],20*1024*1024)
                if hashlib.sha256(payload).hexdigest()!=asset['sha256']:raise ValueError('BGM/SFXが変更されています。')
                if item['start_ms']>=compile_plan(plan,source['timeline'])['duration_ms']:raise ValueError('BGM/SFXの開始位置が動画の終了以降です。')
        selected_ids={e['id'] for p in plans.values() for e in ([p['bgm']] if p['bgm'] else [])+p['sfx']}
        source['warnings']=list(dict.fromkeys(warnings));source['extra']={i:extras[i] for i in selected_ids}
        return source

    def preview(self,timeline_id,plan,index=0,*,time_ms=0):
        timeline=self.timeline(timeline_id);validate_plan(plan,timeline)
        row=compile_plan(plan,timeline)['rows'][index];scene=row['source_scene']
        if not scene['image']:raise ValueError('このコマの採用画像がありません。')
        content=file_bytes(self.store.root,scene['image']['path'],25*1024*1024)
        if hashlib.sha256(content).hexdigest()!=scene['image']['sha256']:raise ValueError('画像が変更されています。タイムラインを保存し直してください。')
        image,geometry=canvas(content,FORMATS[plan['kind']],row['edit'],plan['subtitle_position'],reserve_hook=index==0 and bool(plan['hook']))
        font=find_japanese_font(plan['font_path'])
        for unit in row['utterances']:
            if unit['start_ms']<=row['start_ms']+time_ms<unit['end_ms']:
                image=image.convert('RGBA');image.alpha_composite(subtitle_image(unit['text'],image.size,font,plan['subtitle_position']))
        if index==0 and plan['hook'] and time_ms<3000:
            image=image.convert('RGBA');image.alpha_composite(subtitle_image(plan['hook'],image.size,font,'top' if plan['subtitle_position']=='bottom' else 'bottom'))
        image.thumbnail((720,720))
        return png(image),geometry

    def add_audio(self,content,filename,*,rights_reviewed,expected_revision):
        if rights_reviewed is not True:raise ValueError('BGM/SFXの利用権を確認してください。')
        extension=Path(filename).suffix.lower().lstrip('.')
        if extension not in ('wav','mp3') or not isinstance(content,bytes) or not 0<len(content)<=20*1024*1024:raise ValueError('BGM/SFXは20MiB以下のWAV/MP3にしてください。')
        identifier=str(uuid4());relative=f'video/audio/{identifier}.{extension}'
        with self.store.lock():
            data=self.store.checked(expected_revision);path=self.store.immutable(relative,content)
            metadata=probe(path,forced_format=extension)
            streams=metadata['streams']
            if len(streams)!=1 or streams[0]['codec_type']!='audio' or int(streams[0]['sample_rate'])>48000 or streams[0]['channels'] not in (1,2) or not 0<float(metadata['format']['duration'])<=600:
                raise ValueError('BGM/SFXは音声のみ・48kHz以下・1/2ch・10分以内にしてください。')
            record=dict(id=identifier,path=relative,extension=extension,sha256=hashlib.sha256(content).hexdigest(),rights_reviewed=True,
                name=Path(filename.replace('\\','/')).name[:150],duration_ms=round(float(metadata['format']['duration'])*1000),created_at=now_iso())
            data['audio'].append(record);self.store.save(data,'extra_audio',asset_id=identifier)
            return record

    def create_job(self,timeline_id,kinds,*,expected_revision,acknowledged=False,force=False):
        from manga.video_jobs import create_job
        return create_job(self,timeline_id,kinds,expected_revision=expected_revision,acknowledged=acknowledged,force=force)

    def run_job(self,job_id):
        from manga.video_jobs import run_job
        return run_job(self,job_id)

    def start(self,job_id):
        import threading
        def worker():
            try:self.run_job(job_id)
            except (ValueError,OSError,StorageError):pass  # State and sanitized error are durable.
        threading.Thread(target=worker,daemon=True,name='manga-video-'+job_id[:8]).start()

    def cancel(self,job_id):
        validate_id(job_id)
        job=next((j for j in self.store.load()['jobs'] if j['id']==job_id),None)
        if not job or job['state'] not in ('queued','running'):raise ValueError('実行中のジョブを選択してください。')
        self.store.immutable(f'video/jobs/{job_id}/cancel',b'cancel')

    def recover(self,job_id):
        from manga.video_jobs import recover
        return recover(self,job_id)

    def export_bytes(self,record):
        from manga.video_renderer import sha_file
        path=self.store.safe(record['path'])
        if not path.is_file() or path.stat().st_size>512*1024*1024 or sha_file(path)!=record['sha256']:raise ValueError('出力ファイルが欠損・変更されています。履歴を残して再出力してください。')
        return path.read_bytes()
