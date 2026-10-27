"""『桜色のタイムカプセル』8ページ24コマ・2名。実通信/実ユーザーデータなし。"""
import io
import json
import socket

import pytest
from PIL import Image

from manga.artwork_models import CHARACTER_ALL_FIELDS
from manga.artwork_storage import ArtworkStore
from manga.character_ai_service import CharacterAIService
from manga.character_ai_gemini import GeminiCharacterAI
from manga.editorial_storage import EditorialStore
from manga.models import Project
from manga.storage import ProjectStore,write_json_atomic
from manga.story_storage import StoryStore
from manga.story_ai_service import StoryAIService
from manga.voice_models import empty_voice
from test_gemini35 import card35,raw35

SECRET='FAKE-CHARACTER-GEMINI-PRIVATE-KEY'


@pytest.fixture
def character_gemini(tmp_path,monkeypatch):
    directory=tmp_path/'data'/'projects'
    monkeypatch.setenv('MANGA_DATA_DIR',str(directory));monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'runtime'/'budget.sqlite3'))
    monkeypatch.setenv('GEMINI_API_KEY',SECRET);monkeypatch.setenv('MANGA_ENABLE_TEXT_API','1');monkeypatch.delenv('MANGA_ENABLE_PAID_STORY',raising=False)
    def forbidden(*args,**kwargs):raise AssertionError('No real network/API calls')
    monkeypatch.setattr(socket,'create_connection',forbidden);monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    project=Project(title='桜色のタイムカプセル（架空テスト）',genre='青春',synopsis='咲と春斗が桜の下で埋めた約束を取り出す。',page_count=8)
    ProjectStore(directory).save(project);pages=[]
    for page in range(1,9):
        panels=[]
        for panel in range(1,4):
            name='咲' if (page*3+panel)%2==0 else '春斗'
            explicit='年齢：17歳、性別：女性、髪色：黒、服装：制服' if name=='咲' else '髪色：茶、服装：紺の制服'
            panels.append(dict(panel=panel,description=f'{name}が桜の下で手紙を持つ。{explicit}。',dialogue=f'{name}：「もう一度、約束しよう」',narration='春の放課後。' if panel==1 else ''))
        pages.append(dict(page=page,summary=f'タイムカプセルを探す{page}ページ。',panels=panels))
    story=dict(schema_version=1,title=project.title,summary=project.synopsis,pages=pages)
    version=StoryStore(directory,project.id).save(story,expected_version_id=None)
    EditorialStore(directory,project.id).finalize(expected_version_id=version['id'],expected_revision=0)
    art=ArtworkStore(directory,project.id);person=art.save_character(dict(name='咲',hair_color='茶',personality='既存の性格'),expected_revision=0)
    stream=io.BytesIO();Image.new('RGB',(32,48),'pink').save(stream,'PNG')
    art.add_reference(person['id'],stream.getvalue(),'咲_既存基準.png','正面',expected_revision=art.load()['revision'])
    root=directory/project.id;write_json_atomic(root/'voice.json',empty_voice(project.id))
    for folder in ('voice','video'):
        path=root/folder/'existing.bin';path.parent.mkdir(exist_ok=True);path.write_bytes(b'preserve existing '+folder.encode())
    text=StoryAIService(directory,project.id)
    text.configure(dict(genre='青春',theme='桜の下の約束',mood='温かい',page_count=8,concept_count=3,max_cycles=5,goal_score=80),card35(free=True),expected_revision=0)
    return project,CharacterAIService(directory,project.id)


def answer(request):
    meta=request['character_ai'];items=[]
    for index,c in enumerate(meta['extraction']['candidates']):
        fields={k:'' for k in CHARACTER_ALL_FIELDS};fields['name']=c['name']
        provenance={k:dict(status='要確認',evidence='') for k in CHARACTER_ALL_FIELDS}
        provenance['name']=dict(status='ストーリー明記',evidence=c['sources'][0]['quote'])
        for key,values in c['explicit_fields'].items():
            if len({v['value'] for v in values})==1:
                fields[key]=values[0]['value'];provenance[key]=dict(status='ストーリー明記',evidence=values[0]['source']['quote'])
        for key,value in dict(personality='慎重だが約束を守るという案',visual_identity='桜形の留め具' if index==0 else '青い腕時計',
                              relationships='互いに幼なじみという提案（脚本では未設定）').items():
            fields[key]=value;provenance[key]=dict(status='AI提案',evidence='創作補助案。脚本の事実ではない。')
        items.append(dict(id=c['id'],fields=fields,existing_id=None,provenance=provenance,questions=['人物の同定・外見・関係を人間が確認してください。']))
    return dict(schema_version=1,request_id=request['id'],project_id=request['project_id'],story_version_id=request['story_version_id'],kind='characters',items=items)


def fake_adapter(service,*,mutate=None,fail=None):
    calls=[]
    def send(body,key):
        assert key==SECRET;calls.append(body)
        if fail:raise fail
        request=service.proposals.load()['requests'][-1];value=answer(request)
        if mutate:value=mutate(value,request)
        return raw35(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
    return GeminiCharacterAI(send=send),calls


def generate(workspace,*,adapter=None,**kwargs):
    project,service=workspace
    plan=service.preview_gemini(project,service.proposals.latest()['id'],**kwargs)
    return service.generate_gemini(project,service.proposals.latest()['id'],approval=plan['approval_key'],expected_revision=plan['revision'],adapter=adapter,**kwargs)


def adopt(workspace,result,index=0,*,existing_id=None,fields=None,values=None,**kw):
    _,service=workspace;item=result['proposal']['payload']['items'][index]
    return service.proposals.adopt_fields(result['proposal']['id'],item['id'],values or item['fields'],fields or ['name','personality','relationships','visual_identity'],existing_id,
        expected_revision=service.proposals.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],
        expected_editorial_revision=service.proposals.editorial.load()['revision'],expected_character_revision=service.store.load()['revision'],identity_confirmed=True,**kw)
