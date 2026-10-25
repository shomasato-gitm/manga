import copy
import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from manga.automation import Automation
from manga.storage import StorageError,ProjectStore
from manga.production_dashboard import dashboard
from manga.voice_models import digest
from scripts.verify_production import prepare


@pytest.fixture
def prepared(tmp_path,monkeypatch):
    monkeypatch.setattr(socket,'create_connection',lambda *a,**k:pytest.fail('external network'))
    monkeypatch.setattr('manga.budget.BudgetedExecutor.run',lambda *a,**k:pytest.fail('paid gate'))
    p,s,t,_=prepare(tmp_path/'offline')
    return p,s,t,Automation(s.directory,p.id)


def test_offline_order_real_outputs_restart_delta_and_budget_protection(prepared):
    p,s,t,e=prepared
    original={f:f.read_bytes() for f in (e.root/'story_history.json',e.root/'artwork.json',e.root/'editorial.json')}
    plan=e.plan()
    assert [a['kind'] for a in plan['actions']]==['page']*8
    with pytest.raises(ValueError):e.run(plan['token'])
    jobs=e.run(plan['token'],approved=True,batch=True)
    assert [j['action']['kind'] for j in jobs]==['page']*8+['pdf','png_zip']
    assert all(f.read_bytes()==b for f,b in original.items())
    reopened=Automation(e.directory,p.id)
    assert not reopened.plan()['actions']
    assert '確認' in ' '.join(reopened.plan()['blocked'])
    # 人間が保存済みの3形式計画の編集・権利確認を行ったことを模擬。
    plans=s.plans(t)
    for plan in plans.values(): plan.update(editor_reviewed=True,rights_reviewed=True)
    s.save_plans(t['id'],plans,expected_revision=s.store.load()['revision'])
    jobs=reopened.run(reopened.plan()['token'],approved=True,batch=True)
    assert [j['action']['kind'] for j in jobs]==['video']*3+['inspect']
    assert len(s.store.load()['exports'])==3
    assert not reopened.run(reopened.plan()['token'],approved=True,batch=True)
    assert dashboard(p,e.directory)['rows'][-1]['state']!='完了'
    # 配置1ページ変更：そのページPNGと書籍だけを再生成。音声は増やさない。
    from manga.page_storage import PageStore
    store=PageStore(e.directory,p.id);data=store.load();config=copy.deepcopy(data['pages']['2'])
    config['image_placements']['1']['x']=0.4
    store.save_page(2,config,story_version_id=t['story_version_id'],expected_revision=data['revision'])
    assert [(a['kind'],a['args'].get('page')) for a in e.plan()['actions']]==[('page',2)]
    before=len(e.voice.store.load()['jobs'])
    e.run(e.plan()['token'],approved=True,batch=True)
    assert len(e.voice.store.load()['jobs'])==before


def test_stale_token_lock_and_failure_resume(prepared,monkeypatch):
    p,s,t,e=prepared;plan=e.plan()
    with e.lock():
        with pytest.raises(StorageError): e.run(plan['token'],approved=True)
    original=e.execute
    monkeypatch.setattr(e,'execute',lambda a:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):e.run(plan['token'],approved=True)
    assert e.history()[-1]['state']=='エラー・復旧待ち'
    monkeypatch.setattr(e,'execute',original)
    assert e.run(e.plan()['token'],approved=True)[0]['state']=='完了'
    with pytest.raises(StorageError):e.run(plan['token'],approved=True)


def test_unfinalized_stops_and_paid_voice_never_falls_back(prepared):
    p,s,t,e=prepared
    data=e.voice.store.load()
    speaker=next(iter(data['voices']))
    from manga.voice_models import voice_settings
    e.voice.save_voices({speaker:voice_settings(dict(provider='aws-polly',engine='standard',voice_id='Mizuki'))},expected_revision=data['revision'])
    assert not any(a['kind'] in ('voice','timeline','video') for a in e.plan()['actions'])
    assert any('有料' in b for b in e.plan()['blocked'])
    latest=e.voice.stories.load_history()[-1]
    e.voice.stories.save(latest['story'],expected_version_id=latest['id'])
    assert not e.plan()['actions']


def test_prompt_bundle_and_manual_sha(prepared):
    p,s,t,e=prepared
    from manga.artwork_storage import ArtworkStore
    art=e.voice.artwork
    # 新しい指示を保存して画像を要再確認にする。
    latest=e.voice.stories.load_history()[-1];data=art.load()
    direction=copy.deepcopy(data['panels']['1:1']['direction']);direction['composition']+='\n手紙を中央に'
    art.save_direction(1,1,direction,story_version_id=latest['id'],expected_revision=data['revision'])
    plan=e.plan();assert plan['actions'][0]['kind']=='prompts'
    e.run(plan['token'],approved=True)
    assert not any(a['kind']=='prompts' for a in e.plan()['actions'])
    import zipfile,io
    event=next(x for x in e.studio.load()['exports'] if x.get('artifact_role')=='prompt_bundle')
    z=zipfile.ZipFile(io.BytesIO(e.studio.export_bytes(event)))
    assert 'p1-c1/receipt.json' in z.namelist() and any(n.startswith('references/') for n in z.namelist())


def test_manual_import_sha_idempotent_and_no_adoption(prepared):
    p,s,t,e=prepared
    from manga.image_ai_service import ImageAIService
    from manga.manual_image_ui import receipt
    from scripts.verify_production import test_image as image_bytes
    import hashlib
    service=ImageAIService(e.directory,p.id);plan=service.plan('panel',page=1,panel=1)
    content=image_bytes('extra');ticket=json.dumps(receipt(plan)).encode()
    before=len(service.store.load()['adoptions'])
    with pytest.raises(ValueError): e.import_manual(plan,ticket,content,'manual.png','wrong')
    sha=hashlib.sha256(content).hexdigest()
    first=e.import_manual(plan,ticket,content,'manual.png',sha)
    assert e.import_manual(plan,ticket,content,'manual.png',sha)['id']==first['id']
    assert len(service.store.load()['adoptions'])==before


def test_budget_file_untouched_by_free_operations(prepared,monkeypatch,tmp_path):
    p,s,t,e=prepared
    budget=tmp_path/'protected-budget.sqlite3';budget.write_bytes(b'existing ledger protected')
    monkeypatch.setenv('MANGA_BUDGET_DB',str(budget))
    e.run(e.plan()['token'],approved=True)
    assert budget.read_bytes()==b'existing ledger protected'


def test_one_panel_image_only_schedules_its_page(prepared):
    p,s,t,e=prepared
    e.run(e.plan()['token'],approved=True,batch=True)
    from scripts.verify_production import test_image as image_bytes
    art=e.voice.artwork;version=e.voice.stories.load_history()[-1]
    image=art.add_panel_image(3,2,image_bytes('replacement'),'new.png',story_version_id=version['id'],expected_revision=art.load()['revision'])
    art.select_panel_image(3,2,image['id'],expected_revision=art.load()['revision'])
    assert [(a['kind'],a['args'].get('page')) for a in e.plan()['actions'] if a['kind']=='page']==[('page',3)]
