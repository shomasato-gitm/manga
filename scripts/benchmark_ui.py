"""一時データで画面再実行を比較する。利用者データには触れない。"""
import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from streamlit.testing.v1 import AppTest
from manga.artwork_storage import ArtworkStore
from manga.assistant_storage import AssistantStore
from manga.assistant_samples import sample_response
from manga.models import Project
from manga.prompts import story_template
from manga.storage import ProjectStore
from manga.story_storage import StoryStore


def fixture(root):
    project = Project(title="計測用の漫画", genre="幻想", synopsis="図書館を旅する。")
    ProjectStore(root).save(project)
    story = story_template(project)
    for page in story["pages"]:
        for panel in page["panels"]:
            panel.update(description="ルナが図書館を歩く。", dialogue="ルナ：本を探そう。")
    latest = StoryStore(root, project.id).save(story, expected_version_id=None)
    artwork = ArtworkStore(root, project.id)
    character = artwork.save_character(dict(name="ルナ", hair_color="銀色"), expected_revision=0)
    for page in story["pages"]:
        for panel in page["panels"]:
            artwork.save_direction(page["page"], panel["panel"], dict(character_ids=[character["id"]], location="図書館"), story_version_id=latest["id"], expected_revision=artwork.load()["revision"])
            for number in range(3):
                output = BytesIO()
                Image.new("RGB", (256,384), (80+number*40,100,150)).save(output,"PNG")
                image = artwork.add_panel_image(page["page"],panel["panel"],output.getvalue(),"demo.png",story_version_id=latest["id"],expected_revision=artwork.load()["revision"])
            artwork.select_panel_image(page["page"],panel["panel"],image["id"],expected_revision=artwork.load()["revision"])
    assistant = AssistantStore(root, project.id)
    request = assistant.create_request(project,"directions",expected_revision=0)
    assistant.import_proposal(request["id"],json.dumps(sample_response(request),ensure_ascii=False),expected_revision=1)


def measure(app_path, repeats=9):
    with tempfile.TemporaryDirectory(prefix="manga-ui-benchmark-") as temporary:
        root=Path(temporary);fixture(root)
        previous=os.environ.get("MANGA_DATA_DIR");os.environ["MANGA_DATA_DIR"]=str(root)
        counters={"json_reads":0,"image_reads":0}
        read_text, image_bytes=Path.read_text,ArtworkStore.image_bytes
        def counted_text(path,*args,**kwargs):
            if path.suffix==".json" and path.is_relative_to(root):counters["json_reads"]+=1
            return read_text(path,*args,**kwargs)
        def counted_image(store,*args,**kwargs):
            counters["image_reads"]+=1
            return image_bytes(store,*args,**kwargs)
        try:
            with patch.object(Path,"read_text",counted_text),patch.object(ArtworkStore,"image_bytes",counted_image):
                app=AppTest.from_file(str(app_path),default_timeout=30).run()
                next(b for b in app.button if b.label=="選択したプロジェクトを開く").click().run()
                results={}
                navigation=next((r for r in app.radio if r.label=="制作メニュー"), None)
                stage7=navigation is not None and "企画・ストーリー" in navigation.options
                screens=["ホーム・進行状況","キャラクター設定","キャラクター設定（手動）","漫画ページ編集","漫画ページ編集（プレビュー後）","イラスト制作"] if navigation else ["全機能の縦長画面"]
                for screen in screens:
                    if navigation:
                        menu,section={
                            "ホーム・進行状況":("企画・ストーリー","ホーム・進行状況") if stage7 else ("ホーム・進行状況",None),
                            "キャラクター設定":("キャラクター・作風" if stage7 else "キャラクター設定","AI一括生成・採用"),
                            "キャラクター設定（手動）":("キャラクター・作風" if stage7 else "キャラクター設定","手動設定"),
                            "漫画ページ編集":("漫画ページ編集",None),
                            "漫画ページ編集（プレビュー後）":("漫画ページ編集",None),
                            "イラスト制作":("イラスト制作","コマ制作ワークスペース") if stage7 else ("画像制作・画像管理","コマ画像・採用一覧"),
                        }[screen]
                        next(r for r in app.radio if r.label=="制作メニュー").set_value(menu).run()
                        if section:
                            next(s for s in app.selectbox if s.label=="作業を選択").set_value(section).run()
                        if screen=="漫画ページ編集（プレビュー後）":
                            next(b for b in app.button if b.label=="ページをプレビュー・再描画").click().run()
                    from manga.ui_data import clear_cache
                    clear_cache()
                    counters.update(json_reads=0,image_reads=0)
                    app.run()
                    cold=dict(cold_json_reads=counters["json_reads"],cold_image_reads=counters["image_reads"])
                    times=[];reads=[];images=[]
                    for _ in range(repeats):
                        counters.update(json_reads=0,image_reads=0)
                        started=time.perf_counter();app.run();times.append((time.perf_counter()-started)*1000)
                        assert not app.exception, app.exception
                        reads.append(counters["json_reads"]);images.append(counters["image_reads"])
                    results[screen]=dict(**cold, median_ms=round(statistics.median(times),2), json_reads=statistics.median(reads), image_reads=statistics.median(images),
                                         widgets=sum(len(getattr(app,kind)) for kind in ("text_input","text_area","selectbox","multiselect","checkbox","number_input","button","radio")), samples_ms=[round(t,2) for t in times])
                if navigation:
                    def cycle():
                        # 指示・プロンプト・画像を確認する際の実際の画面操作。保存・生成時間は含めない。
                        if stage7:
                            app.run()
                            return 1
                        count=0
                        for menu,section in (("作画指示・AI評価改善","コマごとの編集"),
                                             ("画像制作・画像管理","画像プロンプト一括出力"),
                                             ("画像制作・画像管理","コマ画像・採用一覧")):
                            nav=next(r for r in app.radio if r.label=="制作メニュー")
                            if nav.value!=menu:nav.set_value(menu).run();count+=1
                            selector=next(s for s in app.selectbox if s.label=="作業を選択")
                            if selector.value!=section:selector.set_value(section).run();count+=1
                        assert not app.exception
                        return count
                    cycle()
                    times=[];reads=[];images=[];reruns=[]
                    for _ in range(repeats):
                        counters.update(json_reads=0,image_reads=0)
                        started=time.perf_counter();reruns.append(cycle());times.append((time.perf_counter()-started)*1000)
                        reads.append(counters["json_reads"]);images.append(counters["image_reads"])
                    results["指示・プロンプト・画像の確認フロー"]=dict(median_ms=round(statistics.median(times),2),
                        reruns=statistics.median(reruns), screens=1 if stage7 else 3,
                        json_reads=statistics.median(reads),image_reads=statistics.median(images),samples_ms=[round(t,2) for t in times])
                return results
        finally:
            if previous is None:os.environ.pop("MANGA_DATA_DIR",None)
            else:os.environ["MANGA_DATA_DIR"]=previous


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--app",type=Path,default=Path("app.py"));parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    result=measure(args.app);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n");print(json.dumps(result,ensure_ascii=False,indent=2))
