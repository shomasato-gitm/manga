import copy
import pytest
from manga.assistant_storage import AssistantStore
from manga.assistant_samples import sample_response
from manga.review_storage import ReviewStore
from manga.review_context import load_context
from manga.review_prompts import new_request,response_example
from manga.sync_responses import fetch_response,accept_response
from manga.sync_manifest import json_bytes
from test_sync_safety import seeded


def test_assistant_response_from_github_is_only_a_proposal_until_existing_adoption(seeded):
    s,r,p,latest,art,pages,_,head=seeded
    assistant=AssistantStore(s.directory,p.id)
    request=assistant.create_request(p,'characters',expected_revision=0)
    payload=sample_response(request);path=f'works/{p.id}/responses/characters.json'
    r.publish('exchange',head,head,{path:json_bytes(payload)},'mock ChatGPT proposal')
    before=art.path.read_bytes()
    candidate=fetch_response(s,'exchange',path,'assistant',expected_revision=1)
    assert assistant.load()['proposals']==[] and art.path.read_bytes()==before
    accept_response(s,candidate['id'],expected_revision=2)
    proposal=assistant.load()['proposals'][0]
    assert set(proposal['decisions'].values())=={'確認待ち'} and art.path.read_bytes()==before
    assert s.store.load()['candidates'][-1]['status']=='採用済み'


def test_image_review_response_reuses_existing_validation_and_preserves_reviews(seeded):
    s,r,p,latest,art,pages,_,head=seeded
    store=ReviewStore(s.directory,p.id);context=load_context(s.directory,p.id)
    request=new_request(context,[dict(page=1,panel=2)],[])
    store.save_request(request,expected_revision=0)
    path=f'works/{p.id}/responses/image_review.json'
    r.publish('exchange',head,head,{path:json_bytes(response_example(request))},'mock image review')
    candidate=fetch_response(s,'exchange',path,'review',expected_revision=1)
    assert store.load()['ai_reviews']==[]
    accept_response(s,candidate['id'],expected_revision=2)
    data=store.load()
    assert data['ai_requests'][0]==request and len(data['ai_reviews'])==1
    assert not data['ai_reviews'][0]['reviewed'] and data['tasks']==[]
    assert set(data['ai_reviews'][0]['decisions'].values())=={'未確認'}


def test_wrong_request_or_stale_story_is_rejected_before_candidate_save(seeded):
    s,r,p,latest,art,pages,_,head=seeded
    assistant=AssistantStore(s.directory,p.id);request=assistant.create_request(p,'characters',expected_revision=0)
    payload=sample_response(request);payload['project_id']='unknown'
    path=f'works/{p.id}/responses/bad.json';r.publish('exchange',head,head,{path:json_bytes(payload)},'invalid response')
    before=s.store.path.read_bytes()
    with pytest.raises(ValueError):fetch_response(s,'exchange',path,'assistant',expected_revision=1)
    assert s.store.path.read_bytes()==before and assistant.load()['proposals']==[]
