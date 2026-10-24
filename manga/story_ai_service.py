"""企画・候補脚本の制作API。正式採用は別の明示操作にする。"""
import copy
import os
from decimal import Decimal
from uuid import uuid4

from manga.artwork_storage import ArtworkStore
from manga.editorial_storage import EditorialStore,generation_references
from manga.story_storage import StoryStore
from manga.storage import ProjectStore,StorageError
from manga.models import now_iso,validate_id
from manga.voice_models import digest
from manga.story_ai_models import settings
from manga.story_ai_storage import AIStoryStore
from manga.story_ai_pricing import evidence
from manga.budget.config import open_budget


class StoryAIService:
    def __init__(self,directory,project_id,*,adapter=None):
        self.directory=directory;self.project_id=validate_id(project_id);self.adapter=adapter
        self.store=AIStoryStore(directory,project_id);self.stories=StoryStore(directory,project_id)
        self.editorial=EditorialStore(directory,project_id);self.artwork=ArtworkStore(directory,project_id)

    def source(self,config):
        project=ProjectStore(self.directory).load(self.project_id);history=self.stories.load_history()
        art=self.artwork.load();editorial=self.editorial.load()
        context=dict(project=project.to_dict(),characters=[c for c in art['characters'] if not c['deleted_at']],
            references=generation_references(editorial,config['reference_ids']),base_story_id=history[-1]['id'] if history else None,
            artwork_revision=art['revision'],editorial_revision=editorial['revision'])
        generation={k:config[k] for k in ('genre','theme','mood','page_count','concept_count','reference_ids')}
        return context,digest(dict(context=context,generation=generation))

    def configure(self,values,card=None,*,expected_revision):
        config=settings(values)
        if card is not None:evidence(card)
        with self.store.lock():
            data=self.store.checked(expected_revision)
            if any(j['state'] in ('queued','running') for j in data['jobs']):raise StorageError('実行中は制作条件を変更できません。停止・完了を確認してください。')
            before=dict(settings=data['settings'],card=data['card'])
            data['settings']=config
            if card is not None:data['card']=copy.deepcopy(card)
            self.store.save(data,'configuration',before=before)

    def select_concept(self,concept_id,*,expected_revision):
        with self.store.source_lock(),self.store.lock():
            data=self.store.checked(expected_revision)
            if any(j['state'] in ('queued','running') for j in data['jobs']):raise StorageError('処理中は企画の選択を変更できません。')
            concept,batch=self.concept(data,concept_id)
            _,token=self.source(data['settings'])
            if token!=batch['source_token']:raise ValueError('企画生成時から作品条件・人物・脚本等が変わりました。企画を再作成してください。')
            data['selections'].append(dict(id=str(uuid4()),concept_id=concept_id,batch_id=batch['id'],created_at=now_iso(),source_token=token))
            self.store.save(data,'concept_selected',concept_id=concept_id)

    @staticmethod
    def concept(data,identifier):
        for batch in data['concept_batches']:
            for concept in batch['ideas']:
                if concept['id']==identifier:return copy.deepcopy(concept),batch
        raise ValueError('選択した企画が見つかりません。')

    def estimate(self,kind,*,provider='mock',spend_cap='500',new_request=False):
        if kind not in ('concepts','story') or provider not in ('mock','openai-responses'):raise ValueError('依頼の種類・プロバイダーを確認してください。')
        data=self.store.load()
        if data['settings'] is None:raise ValueError('先に制作条件を保存してください。')
        config=data['settings'];context,token=self.source(config);selected=None
        if kind=='story':
            if not data['selections']:raise ValueError('先に企画を1案選択してください。')
            selected=data['selections'][-1]['concept_id'];_,batch=self.concept(data,selected)
            if batch['source_token']!=token:raise ValueError('選択企画の元データが古くなっています。')
        calls=1 if kind=='concepts' else 2+2*config['max_cycles']
        cap=Decimal(str(spend_cap))
        if not cap.is_finite() or not 0<cap<=500:raise ValueError('依頼上限は0円より大きく500円以下にしてください。')
        per=Decimal('0') if provider=='mock' else Decimal(evidence(data['card'])['computed_max_jpy'])
        key=digest(dict(kind=kind,provider=provider,settings=config,source_token=token,selected_concept_id=selected,card=data['card'] if provider!='mock' else None,spend_cap=str(cap)))
        return dict(kind=kind,provider=provider,settings=copy.deepcopy(config),context=context,source_token=token,selected_concept_id=selected,
            card=copy.deepcopy(data['card']) if provider!='mock' else None,max_calls=calls,per_call_max_jpy=str(per),full_plan_max_jpy=str(per*calls),
            spend_cap_jpy=str(cap),full_plan_fits=per*calls<=cap,job_key=key,new_request=new_request,approval_key=digest(dict(key=key,new_request=new_request)))

    def create_job(self,kind,*,expected_revision,provider='mock',spend_cap='500',new_request=False,approval=None):
        estimate=self.estimate(kind,provider=provider,spend_cap=spend_cap,new_request=new_request)
        ledger_path=None
        if provider!='mock':
            if not self.paid_enabled() or approval!=estimate['approval_key']:raise ValueError('有料機能フラグと今回の見積・上限への明示承認が必要です。')
            if self.adapter is None:
                from manga.story_ai_provider import api_key
                api_key()
            ledger=open_budget(self.directory);ledger_path=str(ledger.path.resolve())
            if Decimal(estimate['per_call_max_jpy'])>Decimal(estimate['spend_cap_jpy']):raise ValueError('1呼出しの最大見積が依頼上限を超えます。未送信で停止しました。')
            from manga.budget import BudgetedExecutor
            if not BudgetedExecutor(ledger).dry_run(project_id=self.project_id,quote_jpy=estimate['per_call_max_jpy'],provider=provider)['possible']:raise ValueError('作品・月間予算、または請求確認待ちにより実行できません。')
        with self.store.source_lock(),self.store.lock():
            data=self.store.checked(expected_revision)
            if self.source(data['settings'])[1]!=estimate['source_token']:raise StorageError('制作データが変わりました。見積を確認し直してください。')
            if any(j['state'] in ('queued','running') for j in data['jobs']):raise StorageError('この作品のAI制作が実行中です。')
            paths={j['ledger_path'] for j in data['jobs'] if j['provider']!='mock'}
            if ledger_path and paths and paths!={ledger_path}:raise ValueError('予算台帳の保存先が生成履歴と異なります。元の共有台帳を確認してください。')
            if not new_request:
                previous=next((j for j in reversed(data['jobs']) if j['job_key']==estimate['job_key']),None)
                if previous:return copy.deepcopy(previous)
            identifier=str(uuid4())
            job=dict(id=identifier,created_at=now_iso(),updated_at=now_iso(),state='queued',stop_reason=None,kind=kind,provider=provider,
                settings=estimate['settings'],context=estimate['context'],source_token=estimate['source_token'],selected_concept_id=estimate['selected_concept_id'],
                card=estimate['card'],ledger_path=ledger_path,spend_cap_jpy=estimate['spend_cap_jpy'],job_key=estimate['job_key'],
                phase='concepts' if kind=='concepts' else 'draft',cycle=0,current_candidate_id=None,best_candidate_id=None,error=None,run_epoch=str(uuid4()),
                approval=dict(plan_sha256=estimate['approval_key'],confirmed_at=now_iso(),max_calls=estimate['max_calls']) if provider!='mock' else None)
            data['jobs'].append(job);self.store.save(data,'job_created',job_id=identifier)
            return copy.deepcopy(job)

    def run_job(self,identifier):
        from manga.story_ai_engine import run_job
        return run_job(self,identifier)

    def recover(self,identifier):
        from manga.story_ai_engine import recover
        return recover(self,identifier)

    def start(self,identifier):
        import threading
        def work():
            try:self.run_job(identifier)
            except (ValueError,OSError,StorageError):pass
        threading.Thread(target=work,daemon=True,name='manga-story-ai-'+identifier[:8]).start()

    def cancel(self,identifier):
        validate_id(identifier)
        job=next((j for j in self.store.load()['jobs'] if j['id']==identifier),None)
        if not job or job['state'] not in ('queued','running'):raise ValueError('実行中の依頼を選択してください。')
        self.store.write(f"story_ai/jobs/{identifier}/cancel-{job['run_epoch']}",b'cancel')

    def resume(self,identifier,*,confirmed=False):
        from manga.story_ai_engine import resume
        return resume(self,identifier,confirmed=confirmed)

    def adopt(self,candidate_id,*,expected_revision,expected_story_id,confirmed=False):
        from manga.story_ai_adoption import adopt
        return adopt(self,candidate_id,expected_revision=expected_revision,expected_story_id=expected_story_id,confirmed=confirmed)

    def recover_adoption(self):
        from manga.story_ai_adoption import recover_adoption
        return recover_adoption(self)

    @staticmethod
    def paid_enabled():return os.environ.get('MANGA_ENABLE_PAID_STORY')=='1'
