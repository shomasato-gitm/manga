import copy
import hashlib
import io
import json
from collections import namedtuple
from uuid import uuid4

import pytest
from PIL import Image

from manga.budget.config import open_budget
from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_samples import confirm_bible
from manga.storage import StorageError,write_json_atomic
from image_ai_fixtures import image_workspace,generate,adopt,references_ready,hashes


def png():
    stream=io.BytesIO(); Image.new('RGB',(80,120),'purple').save(stream,'PNG'); return stream.getvalue()


def test_manual_input_keeps_original_and_only_explicit_adoption_changes_artwork(image_workspace):
    _,service,_=image_workspace
    original=png(); before=service.artwork.path.read_bytes()
    candidate=generate(image_workspace,provider='manual',content=original,filename='持込画像.png')
    job=next(j for j in service.store.load()['jobs'] if j['id']==candidate['job_id'])
    assert service.store.read(f"image_ai/jobs/{job['id']}/input.bin")==original
    assert candidate['provider']=='manual' and not candidate['mock'] and service.artwork.path.read_bytes()==before
    registered=adopt(image_workspace,candidate)
    assert service.artwork.load()['panels']['1:1']['selected_image_id']==registered['image_id']
    assert service.candidate_bytes(candidate)==original


@pytest.mark.parametrize('filename', ['../a.png','a/b.png','a\\b.png','line\n.png','bad\x00.png','wrong.jpg'])
def test_filename_injection_or_disguised_extension_is_rejected(image_workspace,filename):
    _,service,_=image_workspace
    before=service.artwork.path.read_bytes(); plan=service.plan('panel',page=1,panel=1)
    with pytest.raises(ValueError): service.create_job(plan,provider='manual',content=png(),filename=filename,expected_revision=0)
    assert service.artwork.path.read_bytes()==before and not service.store.path.exists()


@pytest.mark.parametrize('content', [b'',b'not a png',b'x'*(20*1024*1024+1)])
def test_invalid_or_oversize_manual_image_never_creates_job(image_workspace,content):
    _,service,_=image_workspace
    plan=service.plan('panel',page=1,panel=1)
    with pytest.raises(ValueError): service.create_job(plan,provider='manual',content=content,filename='input.png',expected_revision=0)
    assert not service.store.path.exists()


@pytest.mark.parametrize('stage', ['register-before','register-after','select-before','select-after','metadata-before','metadata-after','journal-done'])
def test_adoption_failure_recovers_same_registration_and_selection_once(image_workspace,monkeypatch,stage):
    _,service,_=image_workspace
    candidate=generate(image_workspace)
    service.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'fixture only',expected_revision=service.store.load()['revision'])
    import manga.artwork_storage as art_module
    import manga.image_ai_adoption as adoption_module
    original_art=art_module.write_json_atomic; original_journal=adoption_module.write_json_atomic; original_save=service.store.save
    failed=[]; writes=[]
    def art_write(path,data):
        writes.append(path)
        target='register' if len(writes)==1 else 'select'
        if stage.startswith(target) and not failed:
            if stage.endswith('after'): original_art(path,data)
            failed.append(True); raise OSError('fixture interruption')
        return original_art(path,data)
    def save(data,kind,**kw):
        if kind=='image_adopted' and stage.startswith('metadata') and not failed:
            if stage.endswith('after'): original_save(data,kind,**kw)
            failed.append(True); raise OSError('fixture metadata interruption')
        return original_save(data,kind,**kw)
    def journal(path,data):
        if stage=='journal-done' and data.get('state')=='done' and not failed:
            failed.append(True); raise OSError('fixture done interruption')
        return original_journal(path,data)
    old=copy.deepcopy(service.artwork.load()['panels']['1:1'])
    monkeypatch.setattr(art_module,'write_json_atomic',art_write); monkeypatch.setattr(service.store,'save',save); monkeypatch.setattr(adoption_module,'write_json_atomic',journal)
    with pytest.raises((OSError,StorageError)): service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],confirmed=True)
    registered_before=[i['id'] for i in service.artwork.load()['panels']['1:1']['candidates'] if i['id'] not in {x['id'] for x in old['candidates']}]
    monkeypatch.setattr(art_module,'write_json_atomic',original_art); monkeypatch.setattr(service.store,'save',original_save); monkeypatch.setattr(adoption_module,'write_json_atomic',original_journal)
    selected=service.recover_adoption(confirmed=True)
    assert service.recover_adoption(confirmed=True) is None
    entry=service.artwork.load()['panels']['1:1']
    assert len(entry['candidates'])==len(old['candidates'])+1 and len(entry['selection_history'])==len(old['selection_history'])+1
    if registered_before: assert selected['image_id']==registered_before[0]
    assert entry['selected_image_id']==selected['image_id'] and len(service.store.load()['adoptions'])==1
    assert service.artwork.image_bytes(next(i for i in entry['candidates'] if i['id']==selected['image_id']))


def leave_pending(workspace,monkeypatch):
    _,service,_=workspace
    candidate=generate(workspace)
    original=service.store.save
    def fail(data,kind,**kw):
        if kind=='image_adopted': raise OSError('fixture interrupted')
        return original(data,kind,**kw)
    monkeypatch.setattr(service.store,'save',fail)
    with pytest.raises((OSError,StorageError)): adopt(workspace,candidate)
    monkeypatch.setattr(service.store,'save',original)
    return service


def test_other_edit_stops_adoption_recovery_without_overwrite(image_workspace,monkeypatch):
    service=leave_pending(image_workspace,monkeypatch)
    service.artwork.save_style('別編集のカラー設定',expected_revision=service.artwork.load()['revision'])
    before=service.artwork.path.read_bytes()
    with pytest.raises(StorageError,match='別の編集'): service.recover_adoption(confirmed=True)
    assert service.artwork.path.read_bytes()==before


def test_tampered_journal_cannot_delete_existing_images(image_workspace,monkeypatch):
    service=leave_pending(image_workspace,monkeypatch)
    path=service.store.safe('image_ai/pending_adoption.json'); value=json.loads(path.read_text())
    value['before_artwork']['panels']['1:1']['candidates']=[]
    value['before_artwork']['panels']['1:1']['selected_image_id']=None
    value['before_artwork']['panels']['1:1']['selection_history']=[]
    write_json_atomic(path,value); before=service.artwork.path.read_bytes()
    with pytest.raises((ValueError,StorageError)): service.recover_adoption(confirmed=True)
    assert service.artwork.path.read_bytes()==before


@pytest.mark.parametrize('stage', ['receipt','metadata-before','metadata-after'])
def test_candidate_save_failure_resumes_same_id_without_duplicate(image_workspace,monkeypatch,stage):
    _,service,_=image_workspace
    plan=service.plan('panel',page=1,panel=1); job=service.create_job(plan,expected_revision=0)
    original_write=service.store.write; original_save=service.store.save
    failed=[]
    def write(relative,content,*a,**kw):
        if relative.endswith('receipt.json') and stage=='receipt' and not failed:
            failed.append(True); raise OSError('fixture receipt interruption')
        return original_write(relative,content,*a,**kw)
    def save(data,kind,**kw):
        if kind=='candidate_saved' and stage.startswith('metadata') and not failed:
            if stage.endswith('after'): original_save(data,kind,**kw)
            failed.append(True); raise OSError('fixture metadata interruption')
        return original_save(data,kind,**kw)
    monkeypatch.setattr(service.store,'write',write); monkeypatch.setattr(service.store,'save',save)
    try: service.run(job['id'])
    except StorageError: pass
    monkeypatch.setattr(service.store,'write',original_write); monkeypatch.setattr(service.store,'save',original_save)
    result=service.recover(job['id'],confirmed=True)
    assert result['id']==job['candidate_id'] and len(service.store.load()['candidates'])==1
    assert not service.store.load()['adoptions']


def test_failed_manual_input_save_resumes_exact_original_bytes(image_workspace,monkeypatch):
    _,service,_=image_workspace; plan=service.plan('panel',page=1,panel=1); original=service.store.write; content=png()
    def fail(relative,payload,*a,**kw):
        if relative.endswith('input.bin'): raise OSError('fixture input interruption')
        return original(relative,payload,*a,**kw)
    monkeypatch.setattr(service.store,'write',fail)
    with pytest.raises((OSError,StorageError)): service.create_job(plan,provider='manual',content=content,filename='input.png',expected_revision=0)
    job=service.store.load()['jobs'][0]; monkeypatch.setattr(service.store,'write',original)
    with pytest.raises(StorageError): service.recover(job['id'],confirmed=True,content=content+b'different')
    result=service.recover(job['id'],confirmed=True,content=content)
    assert result['id']==job['candidate_id'] and len(service.store.load()['jobs'])==1


@pytest.mark.parametrize('relative', ['/tmp/evil.png','../artwork.json','image_ai/../artwork.json','image_ai\\outside.png'])
def test_path_escape_rejected(image_workspace,relative):
    _,service,_=image_workspace
    with pytest.raises(StorageError): service.store.write(relative,b'x')


def test_candidate_symlink_and_hash_change_stop_adoption(image_workspace,tmp_path):
    _,service,_=image_workspace; candidate=generate(image_workspace)
    path=service.store.safe(candidate['path']); outside=tmp_path/'outside.png'; outside.write_bytes(path.read_bytes())
    path.unlink(); path.symlink_to(outside)
    with pytest.raises(StorageError,match='リンク'): service.candidate_bytes(candidate)
    path.unlink(); path.write_bytes(outside.read_bytes()+b'modified')
    with pytest.raises(StorageError,match='SHA'): adopt(image_workspace,candidate)
    assert not service.store.load()['adoptions']


def test_reference_missing_hash_change_and_stale_bible_stop_plans(image_workspace):
    _,service,characters=image_workspace
    image=service.artwork.load()['characters'][0]['references'][0]; path=service.artwork.image_path(image)
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError): service.plan('panel',page=1,panel=1)
    path.unlink()
    with pytest.raises(ValueError): service.plan('panel',page=1,panel=1)


def test_low_disk_and_missing_metadata_do_not_reset_history(image_workspace,monkeypatch):
    _,service,_=image_workspace; plan=service.plan('panel',page=1,panel=1)
    import manga.image_ai_service as module
    disk=namedtuple('disk','total used free')
    with monkeypatch.context() as patch:
        patch.setattr(module.shutil,'disk_usage',lambda p:disk(1,1,0))
        with pytest.raises(StorageError,match='空き容量'): service.create_job(plan,expected_revision=0)
    candidate=generate(image_workspace); service.store.path.unlink(); before=hashes(service.store.root)
    with pytest.raises(StorageError,match='初期化'): service.store.load()
    assert hashes(service.store.root)==before


def test_cancelled_job_preserves_history_and_never_creates_candidate(image_workspace):
    _,service,_=image_workspace
    job=service.create_job(service.plan('panel',page=1,panel=1),expected_revision=0)
    service.cancel(job['id'])
    with pytest.raises(ValueError): service.run(job['id'])
    assert service.store.load()['jobs'][0]['state']=='cancelled' and not service.store.load()['candidates']


@pytest.mark.parametrize('provider',['openai-images','gemini-developer-api','paid-locked'])
def test_all_paid_providers_are_locked_even_with_environment_keys(image_workspace,monkeypatch,provider):
    _,service,_=image_workspace
    ledger=open_budget(service.directory)
    before=ledger.path.read_bytes()
    monkeypatch.setenv('MANGA_ENABLE_PAID_IMAGE','1'); monkeypatch.setenv('OPENAI_API_KEY','dummy-sensitive-value')
    import manga.story_ai_provider as transport
    def forbidden(*a,**k): raise AssertionError('No image paid adapter exists')
    monkeypatch.setattr(transport,'transport',forbidden)
    plan=service.plan('panel',page=1,panel=1)
    with pytest.raises(ValueError,match='有料画像APIは未対応'): service.create_job(plan,provider=provider,expected_revision=0)
    assert ledger.path.read_bytes()==before and not service.store.path.exists()


@pytest.mark.parametrize('state',['exhausted','uncertain'])
def test_mock_uses_no_reservation_or_reset_when_shared_budget_unavailable(image_workspace,state):
    _,service,_=image_workspace; ledger=open_budget(service.directory)
    identifier=str(uuid4()); ledger.reserve(project_id=service.project_id,request_id=identifier,provider='fixture',model='none',kind='test',max_jpy='500')
    if state=='uncertain': ledger.mark_uncertain(identifier)
    before=ledger.path.read_bytes()
    candidate=generate(image_workspace)
    assert candidate['mock'] and ledger.path.read_bytes()==before


def test_image_bomb_is_rejected_without_new_job(image_workspace,monkeypatch):
    _,service,_=image_workspace; plan=service.plan('panel',page=1,panel=1)
    monkeypatch.setattr(Image,'MAX_IMAGE_PIXELS',1)
    with pytest.raises(ValueError): service.create_job(plan,provider='manual',content=png(),filename='input.png',expected_revision=0)
    assert not service.store.path.exists()


def test_registered_image_repair_refuses_overwriting_existing_pixels(image_workspace):
    _,service,_=image_workspace; art=service.artwork.load(); image=art['panels']['1:1']['candidates'][0]
    stream=io.BytesIO(); Image.new('RGB',(image['width'],image['height']),'black').save(stream,'PNG'); content=stream.getvalue()
    before=hashes(service.store.root)
    with pytest.raises(StorageError,match='上書き'): service.artwork.restore_registered_image(image,content,hashlib.sha256(content).hexdigest(),expected_revision=art['revision'])
    assert hashes(service.store.root)==before


def test_offline_timeout_does_not_log_exception_secret_or_call_api(image_workspace,monkeypatch):
    _,service,_=image_workspace; plan=service.plan('panel',page=1,panel=1); job=service.create_job(plan,expected_revision=0)
    import manga.image_ai_jobs as jobs
    original=jobs.mock_image
    def fail(*args): raise TimeoutError('Bearer dummy-secret-do-not-save')
    monkeypatch.setattr(jobs,'mock_image',fail)
    with pytest.raises(StorageError): service.run(job['id'])
    assert 'dummy-secret' not in service.store.path.read_text()
    monkeypatch.setattr(jobs,'mock_image',original)
    candidate=service.recover(job['id'],confirmed=True)
    assert candidate['id']==job['candidate_id'] and service.store.load()['jobs'][0]['api_calls']==0
