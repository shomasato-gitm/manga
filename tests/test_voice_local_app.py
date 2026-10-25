import copy
import json

from manga.storage import ProjectStore
from manga.models import Project
from voice_fixtures import voice_workspace,all_ids
from test_voice_app import open_voice,view
from test_voice_local import manual_setup,source_wav,rights,imported
from test_voicevox import EngineFake,settings
from ui_helpers import button,go


class Upload:
    def __init__(self,content,name):self.content=content;self.name=name;self.size=len(content)
    def getvalue(self):return self.content


def fill_rights(app):
    values={'ソフトの利用規約URL・根拠':rights()['software_terms'],'音声ライブラリ・録音の利用条件URL・許諾根拠':rights()['library_terms'],
            '出典・話者名・取り込み元':rights()['source'],'必要なクレジット（不要なら根拠を記入）':rights()['credit']}
    for widget in app.text_input:
        if widget.label in values:widget.set_value(values[widget.label])
    next(c for c in app.checkbox if c.label=='ソフトの利用条件を確認した').check()
    next(c for c in app.checkbox if c.label=='音声固有の商用利用・配信条件と必要なクレジットを確認した').check()


def test_offline_open_mac_notice_manual_choice_and_input_retention(voice_workspace,monkeypatch):
    import manga.voicevox as module
    service,*_=voice_workspace
    monkeypatch.setattr(module,'_transport',lambda *a:(_ for _ in ()).throw(AssertionError('no connect on render')))
    app=open_voice(service)
    assert any('macOS 12の動作は未確認' in i.value for i in app.info)
    assert next(b for b in app.button if b.label=='ローカルエンジンと話者を確認').disabled
    for box in app.selectbox:
        if box.label.endswith('のプロバイダー'):box.set_value('manual-wav')
    app.run();button(app,'人物別の声設定を保存')
    assert all(s['provider']=='manual-wav' for s in service.store.load()['voices'].values())
    view(app,'音声生成・試聴')
    next(t for t in app.text_input if t.label=='必要なクレジット（不要なら根拠を記入）').set_value('保持するクレジット').run()
    view(app,'準備・話者設定');view(app,'音声生成・試聴')
    assert next(t for t in app.text_input if t.label=='必要なクレジット（不要なら根拠を記入）').value=='保持するクレジット'
    assert not app.exception and not service.store.load()['jobs']


def test_wav_upload_wrong_target_review_then_separate_assign_and_navigation(voice_workspace,monkeypatch):
    import streamlit as native
    service,*_=voice_workspace;manual_setup(service);identifier=all_ids(service)[0]
    uploads={};monkeypatch.setattr(native,'file_uploader',lambda label,**kw:uploads.get(label))
    app=open_voice(service);view(app,'音声生成・試聴');fill_rights(app)
    uploads['この発話のWAV']=Upload(source_wav(),'original.wav')
    wrong=service.voice_target(identifier);wrong['speaker_id']=all_ids(service)[-1]
    uploads['音声の対象確認票JSON']=Upload(json.dumps(wrong).encode(),'target.json');app.run()
    button(app,'WAVを候補として取り込む')
    assert app.error and not service.store.load()['assets']
    uploads['音声の対象確認票JSON']=Upload(json.dumps(service.voice_target(identifier)).encode(),'target.json');app.run()
    button(app,'WAVを候補として取り込む')
    assert not app.error and len(service.store.load()['assets'])==1
    assert next(b for b in app.button if b.label=='この発話へ正式割り当て').disabled
    next(c for c in app.checkbox if c.label=='この候補を試聴し、人物・日本語の読み・内容・音量を確認した').check().run()
    button(app,'音声の試聴確認を記録')
    assert service.store.load()['audio_reviews'] and not service.store.load()['assignments']
    button(app,'この発話へ正式割り当て')
    asset=service.plan()['rows'][0]['asset'];assert asset
    go(app,'企画・ストーリー','比較・脚本確定');go(app,'完成・書き出し','音声制作・字幕');view(app,'音声生成・試聴')
    assert any('正式割り当て済み' in s.value for s in app.success)
    assert service.plan()['rows'][0]['asset']['id']==asset['id']
    before=service.store.path.read_bytes()
    other=Project(title='別作品',genre='検証',synopsis='空の独立作品');ProjectStore(service.directory).save(other)
    other_service=type(service)(service.directory,other.id)
    assert other_service.store.load()['assets']==[] and service.store.path.read_bytes()==before
    app.run()
    next(s for s in app.selectbox if s.label=='プロジェクトを選択').set_value(other.id).run()
    button(app,'選択したプロジェクトを開く')
    next(s for s in app.selectbox if s.label=='プロジェクトを選択').set_value(service.project_id).run()
    button(app,'選択したプロジェクトを開く')
    go(app,'完成・書き出し','音声制作・字幕');view(app,'音声生成・試聴')
    assert service.store.path.read_bytes()==before and not app.exception


def test_stale_story_rejects_ui_upload_and_old_candidate(voice_workspace,monkeypatch):
    import streamlit as native
    service,cast,script,version=voice_workspace;manual_setup(service);identifier=all_ids(service)[0]
    target=service.voice_target(identifier);asset=imported(service)
    changed=copy.deepcopy(script);changed['pages'][0]['panels'][0]['narration']='変更したセリフ。'
    service.stories.save(changed,expected_version_id=version['id'])
    uploads={'この発話のWAV':Upload(source_wav(),'old.wav'),'音声の対象確認票JSON':Upload(json.dumps(target).encode(),'old.json')}
    monkeypatch.setattr(native,'file_uploader',lambda label,**kw:uploads.get(label))
    app=open_voice(service);view(app,'音声生成・試聴');fill_rights(app);app.run()
    button(app,'WAVを候補として取り込む')
    assert app.error and '脚本版' in app.error[0].value
    assert not any(b.label=='この発話へ正式割り当て' for b in app.button)
    assert len(service.store.load()['assets'])==1 and service.audio_bytes(asset)


def test_fake_local_discovery_and_preview_only_on_buttons(voice_workspace,monkeypatch):
    import manga.voicevox as module
    import manga.voice_ui_local as ui
    service,*_=voice_workspace;fake=EngineFake()
    monkeypatch.setattr(module,'_transport',fake);monkeypatch.setattr(ui,'local_available',lambda:True)
    app=open_voice(service);assert not fake.calls
    next(c for c in app.checkbox if c.label=='このMacのエンジンへ接続し、稼働状況・話者一覧を取得する').check().run()
    assert not fake.calls
    button(app,'ローカルエンジンと話者を確認');assert len(fake.calls)==2
    for box in app.selectbox:
        if box.label.endswith('のプロバイダー'):box.set_value('voicevox')
    app.run();button(app,'人物別の声設定を保存');view(app,'音声生成・試聴')
    fill_rights(app)
    next(c for c in app.checkbox if c.label=='この1発話を、このMacのVOICEVOXで生成する').check().run()
    assert len(fake.calls)==2
    button(app,'1発話のプレビュー音声を生成・保存')
    assert len(fake.calls)==6 and not app.exception and not app.error
    assert len(service.store.load()['assets'])==1 and not service.store.load()['assignments']
