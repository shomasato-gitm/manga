"""無料処理の依存計画と再開。採用・確定・外部送信は実行しない。"""
import copy
import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from manga.models import now_iso
from manga.storage import StorageError, ProjectStore
from manga.studio_storage import StudioStore
from manga.story_storage import StoryStore
from manga.artwork_storage import ArtworkStore
from manga.page_storage import PageStore
from manga.editorial_storage import EditorialStore
from manga.voice_models import digest, voice_settings
from manga.voice_service import VoiceService
from manga.video_service import VideoService
from manga.production_dashboard import dashboard
from manga.production_media import evidence, validate_media

LABELS = {'prompts':'未完成コマのプロンプト・参照画像・確認票ZIP', 'page':'変更ページPNG',
          'pdf':'全ページPDF','png_zip':'全ページPNG ZIP','voice':'無料モック音声の準備',
          'timeline':'字幕・音声タイムライン','video':'FFmpeg MP4','inspect':'完成素材の一括検査'}
DEPENDENCIES = {'prompts':['脚本確定','人物・基準画像・バイブル・作画指示の確認'],
    'page':['脚本確定','対象コマの正式採用','ページ構成保存'],
    'pdf':['全ページPNG'],'png_zip':['全ページPNG'], 'voice':['脚本確定','話者同定'],
    'timeline':['全発話の音声','全コマの採用画像'], 'video':['最新タイムライン','人間が保存した動画編集計画'],
    'inspect':['3形式のMP4']}


class Automation:
    def __init__(self,directory,project_id):
        self.directory=Path(directory); self.project_id=project_id
        self.root=self.directory/project_id; self.studio=StudioStore(directory,project_id)
        self.voice=VoiceService(directory,project_id); self.video=VideoService(directory,project_id)

    def sources(self):
        from manga.studio_assets import asset_token
        from manga.character_ai_storage import CharacterAIStore
        art=self.voice.artwork.load(); pages=PageStore(self.directory,self.project_id).load()
        return digest(dict(project=ProjectStore(self.directory).load(self.project_id).to_dict(),
            story=self.voice.stories.load_history(),artwork=art,pages=pages,editorial=self.voice.editorial.load(),
            bibles=CharacterAIStore(self.directory,self.project_id).load(),assets=asset_token(self.root,art,pages['settings']),video_plans=self.video.store.load()['plans']))

    def history(self):
        jobs=self.studio.load().get('automation_jobs',[])
        if not isinstance(jobs,list) or any(not isinstance(j,dict) or j.get('state') not in ('処理中','完了','エラー・復旧待ち') or not isinstance(j.get('action'),dict) or j['action'].get('kind') not in LABELS for j in jobs):
            raise StorageError('自動処理履歴が不正です。初期化せず確認してください。')
        return jobs

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/'.automation-run.lock'
        if path.is_symlink(): raise StorageError('自動処理ロックのリンクを拒否しました。')
        with path.open('a') as file:
            try: fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise StorageError('別の自動処理が実行中です。') from None
            try: yield
            finally: fcntl.flock(file,fcntl.LOCK_UN)

    def record(self,job):
        def change(data):
            jobs=data.setdefault('automation_jobs',[])
            found=next((j for j in jobs if j['id']==job['id']),None)
            if found: found.update(copy.deepcopy(job))
            else: jobs.append(copy.deepcopy(job))
        self.studio.update(change)

    def _output(self,key):
        for e in reversed(self.studio.load()['exports']):
            if e.get('automation_key')==key:
                try: self.studio.export_bytes(e); return e
                except (ValueError,OSError,StorageError): pass
        return None

    def plan(self):
        project=ProjectStore(self.directory).load(self.project_id)
        state=dashboard(project,self.directory)
        latest=self.voice.stories.load_history()
        actions=[]; blocked=[]
        def add(operation,identity,**args):
            kind=operation
            key=digest(dict(kind=kind,identity=identity))
            actions.append(dict(kind=kind,key=key,args=args,label=LABELS[kind],dependencies=DEPENDENCIES[kind]))
        if state['recoveries']:
            return dict(status='エラー・復旧待ち',actions=[],blocked=[r['label'] for r in state['recoveries']],token=self.sources(),destination=str(self.root))
        if not latest or state['rows'][1]['state']!='完了':
            blocked.append('企画・脚本の正式採用と最新脚本の最終確定が必要です。')
        else:
            latest=latest[-1]; art=self.voice.artwork.load(); pages=PageStore(self.directory,self.project_id).load()
            unfinished=[p for p in state['panels'] if not p['selected'] or p['stale']]
            ready=[p for p in unfinished if p['prompt_ready']]
            for offset in range(0,len(ready),24):
                chunk=ready[offset:offset+24]
                identity=dict(source=self.sources(),targets=[p['key'] for p in chunk])
                key=digest(dict(kind='prompts',identity=identity))
                if not self._output(key): add('prompts',identity,targets=[(p['page'],p['panel']) for p in chunk])
            if unfinished: blocked.append('コマ画像を手動で取り込み、目視・権利確認と正式採用を行ってください。')
            page_keys=[]; all_pages=True
            from manga.artwork_models import image_context_changed
            from manga.ui_data import signature
            for page in latest['story']['pages']:
                number=page['page']; config=pages['pages'].get(str(number))
                relevant=[p for p in state['panels'] if p['page']==number]
                good=config and config['source_page']==page and all(p['selected'] and not p['stale'] for p in relevant)
                if not good: all_pages=False; continue
                entries={str(c['panel']):art['panels'][f"{number}:{c['panel']}"] for c in page['panels']}
                selected={k:dict(direction=e['direction'],image=next(i for i in e['candidates'] if i['id']==e['selected_image_id'])) for k,e in entries.items()}
                identity=dict(page=page,config=config,settings=pages['settings'],style=art['style'],selected=selected,
                    files={k:signature(self.root/e['image']['path']) for k,e in selected.items()})
                def material(value):
                    if isinstance(value,dict): return {k:material(v) for k,v in value.items() if k not in ('story_version_id','source_version_id','updated_at','saved_at','selected_at')}
                    if isinstance(value,list): return [material(v) for v in value]
                    return value
                identity=material(identity)
                key=digest(dict(kind='page',identity=identity)); page_keys.append(key)
                if not self._output(key): add('page',identity,page=number)
            if not all_pages: blocked.append('未採用・古い画像、または未保存のページ構成があります。')
            elif not any(a['kind']=='page' for a in actions):
                for kind in ('pdf','png_zip'):
                    identity=page_keys
                    if not self._output(digest(dict(kind=kind,identity=identity))): add(kind,identity)
            voice_data=self.voice.store.load(); vp=self.voice.plan()
            if any(v['provider']=='aws-polly' for v in voice_data['voices'].values()):
                blocked.append('既存の有料音声設定があります。自動変更・有料TTSの実行はしません。')
            elif vp['rows'] and not any(r['speaker_id'] is None for r in vp['rows']) and not any(w!='この話者の声設定を保存してください。' for r in vp['rows'] for w in r['warnings']):
                if any(not r['asset'] or r['status']!='再利用可' for r in vp['rows']):
                    if any(v['provider']!='mock' for v in voice_data['voices'].values()):
                        blocked.append('音声制作でVOICEVOX・WAV候補を1発話ずつ準備し、試聴確認・正式割り当てしてください。')
                    else:add('voice',dict(token=vp['token'],rows=[r['text_hash'] for r in vp['rows']]))
                elif all_pages:
                    from manga.voice_timeline import timeline_is_current
                    records=voice_data['timelines']
                    if not records or not timeline_is_current(self.voice,records[-1]): add('timeline',dict(source=self.sources(),voice=voice_data))
                    else:
                        timeline_id=records[-1]['id']; video=self.video.store.load(); plans=video['plans'].get(timeline_id)
                        if not plans: blocked.append('3形式の動画編集計画を人間が確認・保存してください。')
                        elif any(not p['editor_reviewed'] or not p['rights_reviewed'] for p in plans.values()):
                            blocked.append('動画編集計画の編集・素材権利の確認を保存してください。')
                        else:
                            from manga.video_jobs import fingerprint
                            for kind,p in plans.items():
                                try:
                                    src=self.video.inspect(timeline_id,{kind:p}); fp=fingerprint(src,{kind:p})
                                    exports=[e for e in video['exports'] if e['kind']==kind and e['fingerprint']==fp]
                                    valid=False
                                    for e in exports:
                                        try: self.video.export_bytes(e); valid=True; break
                                        except (ValueError,OSError,StorageError): pass
                                    if not valid: add('video',fp,timeline_id=timeline_id,kind=kind)
                                except (ValueError,OSError,StorageError) as error: blocked.append(str(error))
                            if not any(a['kind']=='video' for a in actions) and not evidence(self.directory,self.project_id): add('inspect',self.sources())
            else: blocked.append('話者の同定・読み上げ文を人間が確認してください。')
        token=digest(dict(source=self.sources(),actions=actions))
        status='実行可能' if actions else '要再確認' if state['updated'] else '人間の確認待ち' if blocked else '完了'
        if state['rows'][-1]['state']!='完了' and not blocked: blocked.append('完成品の目視・権利・公開可否の最終確認は人間が行ってください。')
        if not actions and blocked and status=='完了': status='人間の確認待ち'
        if not actions and any('ありません' in b for b in blocked): status='素材不足'
        if self.history() and self.history()[-1]['state']=='エラー・復旧待ち': status='エラー・復旧待ち'
        return dict(status=status,actions=actions,blocked=blocked,token=token,destination=str(self.root))

    def execute(self,action):
        kind=action['kind']; args=action['args']
        latest=self.voice.stories.load_history()[-1]; art=self.voice.artwork.load(); pages=PageStore(self.directory,self.project_id).load()
        from manga.studio_assets import production_fingerprint
        event=dict(kind='sns' if kind=='prompts' else kind,filename=kind+'.zip' if kind in ('prompts','png_zip') else kind+'.png' if kind=='page' else kind+'.pdf',
            fingerprint=production_fingerprint(latest,art,pages,self.root),pages=[args['page']] if kind=='page' else [p['page'] for p in latest['story']['pages']],automation_key=action['key'],artifact_role='prompt_bundle' if kind=='prompts' else 'manga')
        if kind=='page': event['filename']=f"page_{args['page']:03d}.png"
        if kind=='prompts':
            from manga.image_ai_service import ImageAIService
            from manga.manual_image_ui import receipt
            from manga.video_sources import file_bytes
            from manga.page_exports import zip_files
            import hashlib
            images=ImageAIService(self.directory,self.project_id); files=[]; refs=set()
            for p,c in args['targets']:
                plan=images.plan('panel',page=p,panel=c)
                files.extend([(f'p{p}-c{c}/prompt.txt',plan['prompt'].encode()),(f'p{p}-c{c}/receipt.json',json.dumps(receipt(plan),ensure_ascii=False).encode())])
                for ref in plan['reference_images']:
                    if ref['id'] in refs: continue
                    payload=file_bytes(self.root,ref['path'],20*1024*1024)
                    if hashlib.sha256(payload).hexdigest()!=ref['sha256']: raise StorageError('参照画像SHAが変更されました。')
                    refs.add(ref['id']); files.append(('references/'+ref['id']+'.png',payload))
            self.studio.save_export(event,zip_files(files))
        elif kind in ('page','pdf','png_zip'):
            from manga.japanese_text import find_japanese_font
            from manga.page_exports import page_png,export_book
            from manga.page_render import render_page
            font=find_japanese_font(pages['settings']['font_path'])
            if kind=='page': payload=page_png(render_page(latest['story']['pages'][args['page']-1],latest['id'],art,pages,self.voice.artwork,font))
            else: payload,_=export_book(latest['story'],latest['id'],art,pages,self.voice.artwork,font,kind)
            self.studio.save_export(event,payload)
        elif kind=='voice':
            data=self.voice.store.load()
            if any(v['provider']!='mock' for v in data['voices'].values()): raise StorageError('有料音声設定を拒否しました。')
            vp=self.voice.plan(); speakers={r['speaker_id'] for r in vp['rows']}
            if None in speakers: raise ValueError('話者を確認してください。')
            missing={s:voice_settings({}) for s in speakers if s not in data['voices']}
            if missing: self.voice.save_voices(missing,expected_revision=data['revision'])
            self.voice.generate([r['id'] for r in self.voice.plan()['rows']])
        elif kind=='timeline':
            from manga.voice_timeline import save_timeline
            save_timeline(self.voice,expected_revision=self.voice.store.load()['revision'])
        elif kind=='video':
            job=self.video.create_job(args['timeline_id'],[args['kind']],expected_revision=self.video.store.load()['revision'],acknowledged=True)
            if job['state']=='queued': self.video.run_job(job['id'])
        elif kind=='inspect': validate_media(self.directory,self.project_id)
        else: raise ValueError('許可されていない自動処理です。')

    def run(self,token,*,approved=False,batch=False,limit=32):
        if approved is not True or type(limit) is not int or not 1<=limit<=32: raise ValueError('処理内容・対象・保存先の明示承認が必要です。')
        with self.lock():
            plan=self.plan()
            if token!=plan['token']: raise StorageError('制作状態が変更されました。対象を再確認してください。')
            for old in self.history():
                if old['state']=='処理中':
                    old.update(state='エラー・復旧待ち',error='前回の処理が中断しました。再計画して再利用します。'); self.record(old)
            source=self.sources(); completed=[]
            for _ in range(limit if batch else 1):
                if self.sources()!=source: raise StorageError('制作元が変更されました。停止しました。')
                current=self.plan()
                if not current['actions']: break
                action=current['actions'][0]
                job=dict(id=str(uuid4()),key=action['key'],action=action,state='処理中',started_at=now_iso(),error=None)
                self.record(job)
                try:
                    self.execute(action)
                    if self.sources()!=source: raise StorageError('処理中に制作元が変更されました。再確認してください。')
                    job.update(state='完了',finished_at=now_iso()); self.record(job); completed.append(job)
                except Exception:
                    job.update(state='エラー・復旧待ち',error='処理を完了できませんでした。素材・保存先・既存復旧画面を確認してください。',finished_at=now_iso())
                    self.record(job)
                    raise
            return completed

    def start(self,token,*,batch=False):
        import threading
        def worker():
            try: self.run(token,approved=True,batch=batch)
            except (ValueError,OSError,StorageError): pass
        threading.Thread(target=worker,daemon=True,name='manga-automation-'+self.project_id[:8]).start()

    def import_manual(self,plan,ticket,content,filename,expected_sha256):
        """確認票と利用者が指定したSHAを照合し、候補だけを保存する。"""
        import hashlib
        from manga.manual_image_ui import verify_receipt
        from manga.image_ai_service import ImageAIService
        if not isinstance(content,bytes) or hashlib.sha256(content).hexdigest()!=expected_sha256:
            raise ValueError('アップロード画像のSHA-256が一致しません。')
        verify_receipt(plan,ticket)
        service=ImageAIService(self.directory,self.project_id)
        with self.lock():
            job=service.create_job(plan,provider='manual',content=content,filename=filename,expected_revision=service.store.load()['revision'])
            if job['state']=='queued': return service.run(job['id'])
            if job['state']=='ready': return next(c for c in service.store.load()['candidates'] if c['id']==job['candidate_id'])
            raise StorageError('同じ画像依頼の保存復旧が必要です。')
