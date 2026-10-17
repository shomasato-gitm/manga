"""Gitの実ファイル名は変更せず、NFC/NFDの表示差を吸収する。"""
import copy
from pathlib import Path
import unicodedata

import pytest

from manga.github_sync import SyncService
from manga.sync_adoption import image_identity
from test_github_sync import local_remote
from review_fixtures import sample_image

NAME='夕暮れの教室、涙色のまなざし.png'


@pytest.mark.parametrize('stored_form',['NFC','NFD'])
@pytest.mark.parametrize('precompose',['true','false'])
def test_read_both_normalizations_by_actual_blob(local_remote,stored_form,precompose):
    remote=local_remote
    path='works/example/'+unicodedata.normalize(stored_form,NAME)
    image=sample_image()
    commit=remote.publish('exchange',None,None,{path:image},'Unicode fixture')
    remote._run('config','core.precomposeUnicode',precompose)
    assert remote.list_files(commit,'works/example/')=={path:len(image)}
    for requested_form in ('NFC','NFD'):
        assert remote.read(commit,unicodedata.normalize(requested_form,path))==image


def test_unicode_directories_are_resolved_without_renaming(local_remote):
    stored=unicodedata.normalize('NFD','works/example/画像/がぞう/'+NAME)
    commit=local_remote.publish('exchange',None,None,{stored:sample_image()},'Unicode folders')
    assert local_remote.read(commit,unicodedata.normalize('NFC',stored))==sample_image()


@pytest.mark.parametrize('requested_form',['NFC','NFD'])
def test_canonical_name_collision_is_rejected_even_for_exact_match(local_remote,requested_form):
    prefix='works/example/'
    files={prefix+unicodedata.normalize('NFC',NAME):sample_image(),
           prefix+unicodedata.normalize('NFD',NAME):sample_image(color='blue')}
    commit=local_remote.publish('exchange',None,None,files,'Ambiguous names')
    with pytest.raises(ValueError,match='複数'):
        local_remote.read(commit,prefix+unicodedata.normalize(requested_form,NAME))


def test_unicode_alias_never_accepts_a_symlink(local_remote,tmp_path):
    remote=local_remote
    commit=remote.publish('exchange',None,None,{'target.png':sample_image()},'Symlink target')
    env=dict(GIT_INDEX_FILE=str(tmp_path/'index'),GIT_AUTHOR_NAME='Test',GIT_AUTHOR_EMAIL='test@example.invalid',
             GIT_COMMITTER_NAME='Test',GIT_COMMITTER_EMAIL='test@example.invalid')
    remote._run('read-tree',commit,extra_env=env)
    oid=remote._run('hash-object','-w','--stdin',content=b'target.png').decode().strip()
    path=unicodedata.normalize('NFD',NAME)
    remote._run('update-index','--add','--cacheinfo','120000',oid,path,extra_env=env)
    tree=remote._run('write-tree',extra_env=env).decode().strip()
    linked=remote._run('commit-tree',tree,content=b'link fixture\n',extra_env=env).decode().strip()
    with pytest.raises(ValueError,match='通常ファイル'):
        remote.read(linked,unicodedata.normalize('NFC',path))


def test_compatibility_characters_are_not_folded(local_remote):
    commit=local_remote.publish('exchange',None,None,{'works/example/Ａ.png':sample_image()},'Distinct name')
    with pytest.raises(ValueError,match='通常ファイル'):
        local_remote.read(commit,'works/example/A.png')


def test_image_identity_ignores_only_original_filename_normalization():
    old={'id':'same-image','original_name':unicodedata.normalize('NFD',NAME),'saved_at':'old'}
    incoming=dict(old,original_name=unicodedata.normalize('NFC',NAME),saved_at='new')
    before=copy.deepcopy(old)
    assert image_identity(old)==image_identity(incoming)
    assert old==before  # 既存の記録は変更しない。
    assert image_identity(old)!=image_identity(dict(incoming,original_name='別画像.png'))


def test_supplied_three_images_import_via_nfc_and_nfd_keeps_same_candidates(tmp_path,local_remote):
    from manga.artwork_storage import ArtworkStore
    from manga.story_storage import StoryStore
    from manga.sync_manifest import decode_json
    source=Path(__file__).resolve().parents[1]/'works'/'2d83c11f-04ee-4134-a66d-dace81c6f698'
    originals={p:p.read_bytes() for p in source.iterdir() if p.is_file()}
    prefix=f'works/{source.name}/'
    local_remote.publish('exchange',None,None,{prefix+p.name:b for p,b in originals.items()},'Read-only work copy')
    service=SyncService(tmp_path/'client',source.name,local_remote)
    mapping=decode_json(originals[source/'page_01_composition_manifest_2026-10-17.json'])['sources']
    previous=None
    for form in ('NFD','NFC'):
        candidate=service.fetch_legacy('exchange',prefix+'story_test_revision_2026-10-17.json',
            artwork_path=prefix+'artwork_directions_test_2026-10-17.json',
            images=[dict(path=unicodedata.normalize(form,prefix+i['file']),page=1,panel=i['panel']) for i in mapping],
            expected_revision=service.store.load()['revision'])
        assert candidate['status']=='確認待ち'
        service.adopt(candidate['id'],['story','settings','images'],expected_revision=service.store.load()['revision'])
        artwork=ArtworkStore(service.directory,source.name).load()
        images=[image for panel in artwork['panels'].values() for image in panel['candidates']]
        assert len(images)==3
        if previous is not None:assert images==previous
        previous=copy.deepcopy(images)
    assert len(StoryStore(service.directory,source.name).load_history())==1
    assert all(p.read_bytes()==b for p,b in originals.items())
