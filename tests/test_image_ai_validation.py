import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from manga.image_ai_models import settings
from manga.image_ai_pricing import research,output_reference
from manga.storage import StorageError,write_json_atomic
from image_ai_fixtures import image_workspace,generate,hashes


@pytest.mark.parametrize('value',[dict(size='4K'),dict(focus=dict(x=0,y=.5,width=.8,height=.5)),dict(focus=dict(x=float('nan'),y=.5,width=.5,height=.5)),dict(n=24)])
def test_invalid_generation_options(value):
    with pytest.raises(ValueError): settings(value)


def test_research_prices_are_not_execution_quotes():
    raw,expired=research()
    assert all(c['execution_available'] is False and c['maximum_charge_verified'] is False for c in raw['cards'])
    google=next(c for c in raw['cards'] if c['model']=='gemini-3.1-flash-lite-image')
    assert float(output_reference(google,27))==pytest.approx(159.6672)
    openai=next(c for c in raw['cards'] if c['model']=='gpt-image-2.5-flare')
    assert output_reference(openai,27) is None


def test_expired_research_is_detected_without_enabling_paid_execution(monkeypatch):
    from datetime import date as real_date
    import manga.image_ai_pricing as module
    class Later(real_date):
        @classmethod
        def today(cls): return cls(2026,12,1)
    monkeypatch.setattr(module,'date',Later)
    raw,expired=module.research()
    assert expired and all(c['execution_available'] is False for c in raw['cards'])


def test_concurrent_request_uses_revision_lock(image_workspace):
    _,service,_=image_workspace; plan=service.plan('panel',page=1,panel=1)
    def create():
        try: return service.create_job(plan,expected_revision=0)
        except StorageError: return None
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _:create(),range(2)))
    assert len(service.store.load()['jobs'])==1


@pytest.mark.parametrize('mutation',['version','duplicate','path'])
def test_invalid_image_metadata_never_initializes_or_overwrites(image_workspace,mutation):
    _,service,_=image_workspace; generate(image_workspace)
    data=service.store.load()
    if mutation=='version': data['schema_version']=2
    elif mutation=='duplicate': data['candidates'].append(copy.deepcopy(data['candidates'][0]))
    else: data['candidates'][0]['path']='../../outside.png'
    write_json_atomic(service.store.path,data); before=hashes(service.store.root)
    with pytest.raises(StorageError): service.store.load()
    assert hashes(service.store.root)==before


def test_source_symlink_rejected_before_reading_external_data(image_workspace,tmp_path):
    _,service,_=image_workspace
    outside=tmp_path/'external.json'; outside.write_text('{}')
    service.store.path.symlink_to(outside)
    with pytest.raises(StorageError): service.store.load()
    assert outside.read_text()=='{}'
