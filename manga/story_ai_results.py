"""APIとモック共通の検証・保存・PDCA遷移。正式作品には書き込まない。"""
import copy
from uuid import uuid5,UUID

from manga.models import now_iso
from manga.story import load_json_payload
from manga.story_ai_models import normalize_result,regressed
from manga.story_comparison import compare_stories


def candidate(data,identifier):return next(c for c in data['candidates'] if c['id']==identifier)
def evaluation(data,identifier):return next((e for e in reversed(data['evaluations']) if e['candidate_id']==identifier),None)


def apply(service,request_id,response):
    with service.store.lock():
        data=service.store.load();request=next(r for r in data['requests'] if r['id']==request_id)
        job=next(j for j in data['jobs'] if j['id']==request['job_id'])
        if request['state']=='applied':return
        if response['status']!='completed':raise ValueError('APIの拒否・未完了回答です。自動再送せず、保存済み応答と課金を確認してください。')
        payload=normalize_result(request['kind'],load_json_payload(response['text']),request)
        now=now_iso()
        if request['kind']=='concepts':
            ideas=[dict(idea,id=str(uuid5(UUID(request['id']),f'idea:{i}'))) for i,idea in enumerate(payload['ideas'])]
            data['concept_batches'].append(dict(id=request['result_id'],request_id=request_id,job_id=job['id'],source_token=job['source_token'],created_at=now,ideas=ideas))
            job.update(state='completed',phase='done',stop_reason='concepts_ready')
        elif request['kind'] in ('draft','improve'):
            previous=request.get('candidate');changes=compare_stories(previous['story'],payload['story']) if previous else []
            record=dict(id=request['result_id'],request_id=request_id,job_id=job['id'],created_at=now,concept_id=job['selected_concept_id'],cycle=request['cycle'],
                story=payload['story'],page_hooks=payload['page_hooks'],change_reason=payload['change_reason'],warnings=payload['warnings'],
                parent_id=previous['id'] if previous else None,changes=changes,decision='未評価')
            data['candidates'].append(record);job.update(current_candidate_id=record['id'],phase='evaluate' if request['kind']=='draft' else 'reevaluate',cycle=request['cycle'])
        else:
            record=dict(id=request['result_id'],request_id=request_id,job_id=job['id'],candidate_id=request['candidate']['id'],created_at=now,payload=payload)
            data['evaluations'].append(record);current=candidate(data,record['candidate_id'])
            old=evaluation(data,job['best_candidate_id']) if job['best_candidate_id'] else None
            if old and job['settings']['stop_on_regression'] and regressed(old['payload'],payload):
                current['decision']='低下・自動推薦しない';job.update(state='stopped',phase='done',stop_reason='quality_regression')
            elif old and not current['changes']:
                current['decision']='変更なし';job.update(state='stopped',phase='done',stop_reason='no_change')
            else:
                current['decision']='自動推薦候補（未採用）';job['best_candidate_id']=current['id']
                if payload['total_score']>=job['settings']['goal_score']:job.update(state='completed',phase='done',stop_reason='goal_reached')
                elif job['cycle']>=job['settings']['max_cycles']:job.update(state='completed',phase='done',stop_reason='improvement_limit')
                else:job['phase']='improve'
        request['state']='applied';job['updated_at']=now
        service.store.save(data,'response_applied',request_id=request_id,job_id=job['id'])
