"""Hermetic TTS fixtures. Prices, credentials and provider responses are invented."""
import copy
import io
import wave
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from manga.artwork_storage import ArtworkStore
from manga.editorial_storage import EditorialStore
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from manga.voice_audio import MockVoice
from manga.voice_models import narrator_id,voice_settings
from manga.voice_polly import PollyVoice
from manga.voice_service import VoiceService


@pytest.fixture
def voice_workspace(tmp_path,monkeypatch,manga_project,story):
    directory=tmp_path/'data'/'projects'
    monkeypatch.setenv('MANGA_DATA_DIR',str(directory))
    monkeypatch.setenv('MANGA_BUDGET_DB',str(tmp_path/'runtime'/'budget.sqlite3'))
    monkeypatch.delenv('MANGA_ENABLE_PAID_TTS',raising=False)
    for name in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_SESSION_TOKEN'):monkeypatch.delenv(name,raising=False)
    import socket
    def no_network(*a,**kw):raise AssertionError('Real external API calls are prohibited')
    monkeypatch.setattr(socket,'create_connection',no_network)
    ProjectStore(directory).save(manga_project)
    artwork=ArtworkStore(directory,manga_project.id)
    characters=[]
    for name in ('悠真','美咲','蓮'):
        characters.append(artwork.save_character({'name':name},expected_revision=artwork.load()['revision']))
    script=copy.deepcopy(story)
    for page in script['pages']:
        for panel in page['panels']:panel.update(dialogue='',narration='')
    script['pages'][0]['panels'][0].update(dialogue='悠真：「あのさ、美咲。」\n美咲：「先に話してもいい？」',narration='卒業まで、あと7日。')
    script['pages'][0]['panels'][1]['dialogue']='蓮：「ふたりとも、どうした？」'
    script['pages'][0]['panels'][2]['dialogue']='悠真：「何でもないよ。」'
    version=StoryStore(directory,manga_project.id).save(script,expected_version_id=None)
    EditorialStore(directory,manga_project.id).finalize(expected_version_id=version['id'],expected_revision=0)
    service=VoiceService(directory,manga_project.id)
    voices={c['id']:voice_settings(dict(voice_id=f'mock-{i+1}')) for i,c in enumerate(characters)}
    voices[narrator_id(manga_project.id)]=voice_settings(dict(voice_id='mock-4'))
    service.save_voices(voices,expected_revision=0)
    return service,characters,script,version


def all_ids(service):
    return [r['id'] for r in service.plan()['rows']]


def fake_card():
    # Fake unit-test prices; official URLs here test source validation only.
    return dict(schema_version=1,provider='aws-polly',verified_on=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat(),
        pricing_url='https://aws.amazon.com/polly/pricing/',api_url='https://docs.aws.amazon.com/polly/latest/dg/API_SynthesizeSpeech.html',
        terms_url='https://aws.amazon.com/service-terms/',commercial_use_reviewed=True,japanese_reviewed=True,billing_maximum_reviewed=True,
        engines={'neural':dict(per_million_characters_usd='10',fx_jpy_per_usd='160',tax_multiplier='1.10',safety_multiplier='1.25',tax_note='TEST ONLY; not actual pricing')})


def paid_setup(service,monkeypatch,*,usage='3',fail=False):
    voices={k:dict(provider='aws-polly',engine='neural',voice_id='Takumi',speed=1.0,emotion='normal') for k in service.speakers()}
    service.save_voices(voices,expected_revision=service.store.load()['revision'])
    service.save_pricing(fake_card(),expected_revision=service.store.load()['revision'])
    calls=[]
    def send(request):
        calls.append(request)
        if fail:raise TimeoutError('dummy-sensitive-provider-error')
        content=MockVoice().synthesize('確認用',voice_settings({}))
        with wave.open(io.BytesIO(content),'rb') as source:pcm=source.readframes(source.getnframes())
        return pcm,{} if usage is None else {'x-amzn-requestcharacters':usage}
    service.polly=PollyVoice(send=send,keys=dict(AWS_ACCESS_KEY_ID='test-access',AWS_SECRET_ACCESS_KEY='test-secret'))
    monkeypatch.setenv('MANGA_ENABLE_PAID_TTS','1')
    return calls
