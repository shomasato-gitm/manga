"""画像制作の公開入口。無料候補は正規Artworkへ自動登録しない。"""
import copy
import hashlib
import shutil
from uuid import UUID, uuid4, uuid5

from manga.artwork_storage import ArtworkStore
from manga.character_ai_service import CharacterAIService
from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_planning import create_plan, current, target
from manga.image_ai_provider import manual_image, offline_only
from manga.image_ai_storage import ImageAIStore, record
from manga.storage import StorageError
from manga.voice_models import digest


class ImageAIService:
    def __init__(self,directory,project_id,*,live_adapter=None):
        self.directory=directory; self.store=ImageAIStore(directory,project_id); self.project_id=self.store.project_id
        self.artwork=ArtworkStore(directory,project_id)
        self._live_adapter=live_adapter

    @property
    def live_adapter(self):
        if self._live_adapter is None:
            from manga.image_ai_live_provider import GeminiImageAdapter
            self._live_adapter=GeminiImageAdapter()
        return self._live_adapter

    def estimate_live(self,plan,card,consent_reference_ids):
        from manga.image_ai_live_plan import prepare
        with self.store.locked():
            self.store.checked(self.store.load()['revision'])
            return prepare(self,plan,card,consent_reference_ids)[0]

    def create_live_job(self,plan,card,consent_reference_ids,*,approval_token,confirmed=False,expected_revision,new_request=False,scope_id=None):
        from manga.image_ai_live_jobs import create
        return create(self,plan,card,consent_reference_ids,approval_token=approval_token,confirmed=confirmed,expected_revision=expected_revision,new_request=new_request,scope_id=scope_id)

    def save_lite_configuration(self,card,*,expected_revision):
        from manga.gemini_image_scope import save_configuration
        return save_configuration(self,card,expected_revision=expected_revision)

    def lite_configuration(self):
        from manga.gemini_image_scope import configuration
        return configuration(self)

    def preview_lite_scope(self,plans,card,*,project_cap='500'):
        from manga.gemini_image_scope import preview
        return preview(self,plans,card,project_cap=project_cap)

    def approve_lite_scope(self,plans,card,**kwargs):
        from manga.gemini_image_scope import approve
        return approve(self,plans,card,**kwargs)

    def confirm_lite_first(self,candidate_id,**kwargs):
        from manga.gemini_image_scope import confirm_first
        return confirm_first(self,candidate_id,**kwargs)

    def lite_first_confirmed(self,card):
        from manga.gemini_image_scope import has_first_review
        return has_first_review(self,card)

    def find_lite_scope(self,plan,card):
        from manga.gemini_image_scope import find_scope
        return find_scope(self,plan,card)

    def plan(self,kind,*,options=None,**selection):
        with self.store.locked():
            self.store.checked(self.store.load()['revision'])
            return create_plan(self.directory,self.project_id,target(kind,**selection),options)

    def create_job(self,plan,*,provider='mock',expected_revision,new_request=False,content=None,filename=None):
        offline_only(provider)
        if type(new_request) is not bool: raise ValueError('新規作成の確認が不正です。')
        input_info=None
        if provider=='manual':
            normalized=manual_image(content,filename)
            input_info=dict(name=filename,sha256=hashlib.sha256(content).hexdigest(),bytes=len(content))
        with self.store.locked():
            data=self.store.checked(expected_revision)
            current(self.directory,self.project_id,plan,hashed=True)
            if hashlib.sha256(plan['prompt'].encode()).hexdigest()!=plan['prompt_sha256']: raise ValueError('依頼プロンプトが変更されています。計画を再作成してください。')
            size=32*1024*1024 if provider=='mock' else len(content)+len(normalized[0])+8*1024*1024
            if shutil.disk_usage(self.store.root).free<size: raise StorageError('候補保存の空き容量が不足しています。未実行で停止しました。')
            key=digest(dict(provider=provider,plan=plan,input=input_info))
            previous=next((j for j in reversed(data['jobs']) if j['job_key']==key),None)
            if previous and not new_request:
                job=copy.deepcopy(previous)
            else:
                identifier=str(uuid4())
                job=record(request_id=identifier,candidate_id=str(uuid5(UUID(identifier),'candidate')),provider=provider,state='queued',plan=copy.deepcopy(plan),
                           input=input_info,job_key=key,api_calls=0,cost_jpy='0',error=None)
                job['id']=identifier
                data['jobs'].append(job); self.store.save(data,'job_created',job_id=identifier)
            # 先に依頼IDを永続化する。保存失敗でも同じinput SHAで再開できる。
            if provider=='manual': self.store.write(f"image_ai/jobs/{job['id']}/input.bin",content)
            return job

    def run(self,job_id,*,content=None,approved=False):
        job=next((j for j in self.store.load()['jobs'] if j['id']==job_id),None)
        if job and job['provider']=='gemini-live':
            from manga.image_ai_live_jobs import run
            return run(self,job_id,approved=approved)
        from manga.image_ai_jobs import run
        return run(self,job_id,content=content)

    def recover(self,job_id,*,confirmed=False,content=None):
        job=next((j for j in self.store.load()['jobs'] if j['id']==job_id),None)
        if job and job['provider']=='gemini-live':
            from manga.image_ai_live_jobs import recover
            return recover(self,job_id,confirmed=confirmed)
        from manga.image_ai_jobs import recover
        return recover(self,job_id,confirmed=confirmed,content=content)

    def cancel(self,job_id):
        from manga.models import validate_id
        validate_id(job_id)
        job=next((j for j in self.store.load()['jobs'] if j['id']==job_id),None)
        if not job or job['state'] not in ('queued','running','artifact_pending'): raise ValueError('停止する依頼を選んでください。')
        if job['provider']=='gemini-live': raise ValueError('実API依頼の送信中止・予約解除を自動で行いません。送信前の依頼は実行せず、送信済みは請求を照合してください。')
        self.store.write(f'image_ai/jobs/{job_id}/cancel',b'cancel')
        if job['state']=='queued':
            with self.store.locked():
                data=self.store.checked(self.store.load()['revision'])
                current_job=next(j for j in data['jobs'] if j['id']==job_id)
                if current_job['state']=='queued':
                    current_job['state']='cancelled'; self.store.save(data,'job_cancelled',job_id=job_id)

    def candidate_bytes(self,candidate):
        content=self.store.read(candidate['path'])
        if hashlib.sha256(content).hexdigest()!=candidate['sha256']: raise StorageError('候補画像のSHAが一致しません。既存候補を上書きせず確認してください。')
        from manga.images import prepare_image
        _,width,height=prepare_image(content)
        if (width,height)!=(candidate['width'],candidate['height']): raise StorageError('候補画像の寸法が変わっています。')
        return content

    def image_path(self,candidate):
        return self.store.safe(candidate['path'])

    def image_bytes(self,candidate):
        return self.candidate_bytes(candidate)

    def review(self,candidate_id,checks,note,*,expected_revision):
        from manga.artwork_models import text
        if not isinstance(checks,dict) or set(checks)!=set(REVIEW_FIELDS) or any(v is not True for v in checks.values()): raise ValueError('人物・衣装・構図・文字・権利等を人間が確認してください。')
        with self.store.locked():
            data=self.store.checked(expected_revision)
            candidate=next((c for c in data['candidates'] if c['id']==candidate_id),None)
            if not candidate: raise ValueError('画像候補が見つかりません。')
            job=next(j for j in data['jobs'] if j['id']==candidate['job_id'])
            current(self.directory,self.project_id,job['plan'],hashed=True); self.candidate_bytes(candidate)
            row=record(candidate_id=candidate_id,sha256=candidate['sha256'],material_token=job['plan']['material_token'],checks=checks,
                       note=text(note,'人間の確認メモ',2000,True),rights_status='test-only' if candidate['mock'] else 'user-attested')
            data['reviews'].append(row); self.store.save(data,'human_review',review_id=row['id'])
            return row

    def adopt(self,candidate_id,*,expected_revision,expected_artwork_revision,confirmed=False):
        from manga.image_ai_adoption import adopt
        return adopt(self,candidate_id,expected_revision=expected_revision,expected_artwork_revision=expected_artwork_revision,confirmed=confirmed)

    def recover_adoption(self,*,confirmed=False):
        from manga.image_ai_adoption import recover
        return recover(self,confirmed=confirmed)

    def overview(self):
        """利用者が計画を開くときだけ実行。画像を原寸で読み込まない。"""
        from manga.image_ai_planning import materials
        service=CharacterAIService(self.directory,self.project_id)
        latest=service.proposals.latest(); art=self.artwork.load(); data=self.store.load(); rows=[]
        for page in latest['story']['pages']:
            for panel in page['panels']:
                p,c=page['page'],panel['panel']; reason=''; token=None
                try: _,_,token=materials(service,target('panel',page=p,panel=c))
                except (ValueError,OSError,StorageError) as error: reason=str(error)
                jobs=[j for j in data['jobs'] if j['plan']['kind']=='panel' and (j['plan']['page'],j['plan']['panel'])==(p,c)]
                candidates=[i for i in data['candidates'] if any(j['id']==i['job_id'] for j in jobs)]
                fresh={j['id'] for j in jobs if j['plan']['material_token']==token}
                stale=bool(candidates and (reason or not any(i['job_id'] in fresh for i in candidates)))
                entry=art['panels'].get(f'{p}:{c}',{})
                selected=next((i for i in entry.get('candidates',[]) if i['id']==entry.get('selected_image_id')),None)
                if selected:
                    from manga.artwork_models import image_context_changed
                    linked=[a for a in data['adoptions'] if a['image_id']==selected['id']]
                    stale=bool(reason or selected['story_version_id']!=latest['id'] or selected['source_panel']!=panel or image_context_changed(selected,entry,art))
                    if linked: stale |= not any(j['candidate_id']==linked[-1]['candidate_id'] and j['id'] in fresh for j in jobs)
                status='要再確認' if stale else '採用済み（外見は人間確認）' if selected else '候補あり' if candidates else '未準備' if reason else '生成準備済み'
                rows.append(dict(page=p,panel=c,status=status,candidates=len(candidates),reason=reason))
        return rows
