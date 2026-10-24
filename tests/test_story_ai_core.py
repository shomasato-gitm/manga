import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from manga.storage import StorageError
from manga.story_ai_models import settings
from manga.story_ai_mock import MockStoryAI
from manga.story_ai_results import evaluation
from manga.story_ai_provider import payload
from manga.story_comparison import compare_stories
from story_ai_fixtures import ai_workspace,job,concepts,fake_card


def protected(service):
    return {str(p.relative_to(service.store.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in service.store.root.rglob('*')
        if p.is_file() and not p.is_symlink() and p.relative_to(service.store.root).parts[0] not in ('story_ai','story_ai.json','.story-ai.lock','.story-ai-generation.lock')}


@pytest.mark.parametrize('count',[3,4,5])
def test_multiple_concepts_are_distinct_selected_and_reloadable(ai_workspace,count):
    service=ai_workspace;config=service.store.load()['settings'];config['concept_count']=count
    service.configure(config,expected_revision=service.store.load()['revision']);before=protected(service)
    first=concepts(service);data=service.store.load();ideas=data['concept_batches'][0]['ideas']
    assert len(ideas)==count and len({i['id'] for i in ideas})==count and len({i['conflict'] for i in ideas})==count
    assert data['selections'][-1]['concept_id']==first['id'] and protected(service)==before
    created=job(service,'concepts');assert created['id']==data['jobs'][0]['id']
    created=job(service,'concepts',new_request=True);service.run_job(created['id'])
    assert len(service.store.load()['concept_batches'])==2


def test_five_improvements_preserve_existing_story_images_and_evaluation_binding(ai_workspace):
    service=ai_workspace;before=protected(service);idea=concepts(service)
    created=job(service,'story');result=service.run_job(created['id']);data=service.store.load()
    assert result['cycle']==5 and result['stop_reason']=='improvement_limit'
    assert len(data['candidates'])==len(data['evaluations'])==6 and len(data['requests'])==13
    for c in data['candidates']:
        assert c['concept_id']==idea['id'] and len(c['story']['pages'])==8 and sum(len(p['panels']) for p in c['story']['pages'])==24
        assert len(c['page_hooks'])==8 and evaluation(data,c['id'])['payload']['story_version_id']==c['id']
    assert len(compare_stories(data['candidates'][0]['story'],data['candidates'][-1]['story']))>0
    assert protected(service)==before


def test_score_regression_stops_and_keeps_best_candidate(ai_workspace):
    class Worse(MockStoryAI):
        def generate(self,request):
            response=super().generate(request)
            if request['kind']=='evaluation' and request['cycle']:
                value=json.loads(response['text']);value['criteria']['opening']['score']=0;response['text']=json.dumps(value)
            return response
    service=ai_workspace;service.adapter=Worse();concepts(service);created=job(service,'story');result=service.run_job(created['id'])
    data=service.store.load()
    assert result['cycle']==1 and result['stop_reason']=='quality_regression' and result['best_candidate_id']==data['candidates'][0]['id']
    assert data['candidates'][-1]['decision']=='低下・自動推薦しない' and len(service.stories.load_history())==1


def test_goal_and_no_change_stop_without_five_blind_iterations(ai_workspace):
    service=ai_workspace;config=service.store.load()['settings'];config['goal_score']=50
    service.configure(config,expected_revision=service.store.load()['revision']);concepts(service)
    result=service.run_job(job(service,'story')['id'])
    assert result['stop_reason']=='goal_reached' and result['cycle']==0


def test_no_actual_story_change_keeps_previous_recommendation(ai_workspace):
    class Unchanged(MockStoryAI):
        def generate(self,request):
            response=super().generate(request)
            if request['kind']=='improve':
                value=json.loads(response['text']);value['story']=request['candidate']['story'];response['text']=json.dumps(value)
            return response
    service=ai_workspace;concepts(service);service.adapter=Unchanged();result=service.run_job(job(service,'story')['id'])
    data=service.store.load();assert result['stop_reason']=='no_change' and result['best_candidate_id']==data['candidates'][0]['id']


@pytest.mark.parametrize('field,value',[('max_cycles',6),('max_cycles',True),('concept_count',2),('concept_count',6),('page_count',0),('goal_score',float('nan')),('reference_ids',['bad']),('mood','')])
def test_invalid_settings_do_not_overwrite(ai_workspace,field,value):
    service=ai_workspace;config=service.store.load()['settings'];before=service.store.path.read_bytes();config[field]=value
    with pytest.raises(ValueError):service.configure(config,expected_revision=service.store.load()['revision'])
    assert service.store.path.read_bytes()==before


def test_stale_character_or_project_blocks_sending_and_duplicate_jobs(ai_workspace):
    service=ai_workspace;created=job(service,'concepts')
    with pytest.raises(StorageError):job(service,'concepts')
    service.artwork.save_character({'name':'新しい人物'},expected_revision=service.artwork.load()['revision'])
    result=service.run_job(created['id']);assert result['stop_reason']=='stale_sources' and not service.store.load()['requests']


def test_cancel_and_resume_retain_request_ids(ai_workspace):
    service=ai_workspace;created=job(service,'concepts');service.cancel(created['id'])
    result=service.run_job(created['id']);assert result['stop_reason']=='user_cancelled'
    with pytest.raises(ValueError):service.resume(created['id'])
    service.resume(created['id'],confirmed=True);service.run_job(created['id'])
    assert len(service.store.load()['requests'])==1 and len(service.store.load()['concept_batches'])==1


def test_bad_json_or_duplicate_ids_fail_closed(ai_workspace):
    service=ai_workspace;concepts(service);data=service.store.load();data['jobs'].append(data['jobs'][0]);service.store.path.write_text(json.dumps(data))
    with pytest.raises(StorageError):service.store.load()
    service.store.path.write_text('{broken')
    with pytest.raises(StorageError):service.store.load()
    assert service.store.path.read_text()=='{broken'


def test_missing_metadata_with_existing_receipts_does_not_reset_requests(ai_workspace):
    service=ai_workspace;concepts(service);service.store.path.unlink()
    with pytest.raises(StorageError):service.store.load()
    assert not service.store.path.exists() and list(service.store.safe('story_ai/responses').glob('*.json'))


def test_provider_payload_has_strict_schema_no_tools_or_key(ai_workspace):
    service=ai_workspace;created=job(service,'concepts')
    from manga.story_ai_requests import next_request
    request=next_request(service,created['id']);value=payload(request,fake_card())
    assert value['text']['format']['strict'] and value['tools']==[] and not value['store'] and value['truncation']=='disabled'
    assert 'Authorization' not in json.dumps(value) and value['metadata']['manga_request_id']==request['request_id']


def test_wrong_evaluation_version_is_never_applied(ai_workspace):
    class Wrong(MockStoryAI):
        def generate(self,request):
            response=super().generate(request)
            if request['kind']=='evaluation':
                value=json.loads(response['text']);value['story_version_id']=str(__import__('uuid').uuid4());response['text']=json.dumps(value)
            return response
    service=ai_workspace;concepts(service);service.adapter=Wrong();result=service.run_job(job(service,'story')['id'])
    assert result['stop_reason']=='invalid_output' and not service.store.load()['evaluations']
