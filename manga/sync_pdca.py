"""人間の操作でだけ進む改善記録。点数・未確認・上限・停止理由を分ける。"""
import copy
import math
from uuid import uuid4
from manga.models import now_iso
from manga.review_context import load_context
from manga.review_status import summary
from manga.review_storage import ReviewStore


def set_policy(store,max_iterations,minimum_score,*,expected_revision):
    if type(max_iterations) is not int or not 1<=max_iterations<=100 or type(minimum_score) is not int or not 0<=minimum_score<=100:raise ValueError('改善上限は1〜100回、品質基準は0〜100点にしてください。')
    with store.locked():
        data=store.checked(expected_revision)
        store.event(data,'policy',before=copy.deepcopy(data['policy']))
        data['policy']=dict(max_iterations=max_iterations,minimum_score=minimum_score);store.save(data)


def record_iteration(service,candidate_id,*,expected_revision,score=None,confirmed=False,stop_reason=''):
    if score is not None and (isinstance(score,bool) or not isinstance(score,(float,int)) or not math.isfinite(score) or not 0<=score<=100):raise ValueError('点数は未入力または0〜100で指定してください。')
    if type(confirmed) is not bool or not isinstance(stop_reason,str) or len(stop_reason)>2000:raise ValueError('確認状態・停止理由が不正です。')
    store=service.store
    with store.locked(production=True):
        data=store.checked(expected_revision);candidate=service._candidate(data,candidate_id)
        if len(data['iterations'])>=data['policy']['max_iterations']:raise ValueError('改善回数の上限に達しました。自動継続はしません。設定を確認してください。')
        context=load_context(service.directory,service.project_id,cached=False)
        quality=summary(context,ReviewStore(service.directory,service.project_id).load())
        ready=bool(quality['pages'] and quality['finals']==quality['pages'] and not quality['tasks'] and not quality['unconfirmed_ai'])
        current=context.latest and candidate['manifest']['story_version_id']==context.latest['id']
        verified=confirmed and ready and bool(current)
        reached=score is not None and score>=data['policy']['minimum_score']
        reason=stop_reason.strip()
        state='停止' if reason else '品質基準達成' if verified and reached else '上限到達' if len(data['iterations'])+1>=data['policy']['max_iterations'] else '次のGitHub成果物待ち'
        if not reason:reason='利用者の最終確認と点数基準を達成' if state=='品質基準達成' else '改善回数の上限' if state=='上限到達' else '未確認の項目あり' if not verified else '品質基準未達'
        row=dict(id=str(uuid4()),saved_at=now_iso(),candidate_id=candidate_id,source_commit=candidate['source_commit'],
                 story_version_id=candidate['manifest']['story_version_id'],score=score,confirmed=verified,requested_confirmation=confirmed,
                 state=state,stop_reason=reason,quality= {k:v for k,v in quality.items() if k!='states'},policy=copy.deepcopy(data['policy']))
        data['iterations'].append(row);store.save(data);return row
