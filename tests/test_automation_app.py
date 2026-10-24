from pathlib import Path
from streamlit.testing.v1 import AppTest
from image_ai_fixtures import image_workspace
from ui_helpers import button,element


def test_home_preview_approval_and_no_implicit_processing(image_workspace,monkeypatch):
    p,s,_=image_workspace
    from manga.automation import Automation
    calls=[]
    monkeypatch.setattr(Automation,'start',lambda self,token,**kw:calls.append((token,kw)))
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=60).run()
    button(app,'選択したプロジェクトを開く')
    assert not app.exception and not calls
    assert not (s.store.root/'.automation-run.lock').exists()
    assert element(app,'button','次の自動処理を実行').disabled
    element(app,'checkbox','対象・件数・保存先・無料処理の順序と上限を確認し、実行を承認する').check().run()
    button(app,'次の自動処理を実行')
    assert len(calls)==1 and not s.store.load()['adoptions']
