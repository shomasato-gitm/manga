#!/usr/bin/env python3
"""独立した8ページ24コマを企画から手動取り込み・PNG/PDF・実MP4まで検証。"""
import argparse
import io
import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from manga.models import Project
from manga.storage import ProjectStore
from manga.story_ai_service import StoryAIService
from manga.story_ai_mock import MockStoryAI
from manga.character_ai_service import CharacterAIService
from manga.image_ai_service import ImageAIService
from manga.image_ai_models import REVIEW_FIELDS
from manga.manual_image_ui import receipt, verify_receipt
from manga.page_storage import PageStore
from manga.page_layout import default_page
from manga.page_exports import export_book
from manga.studio_storage import StudioStore
from manga.studio_assets import production_fingerprint
from manga.japanese_text import find_japanese_font
from manga.production_dashboard import dashboard
from manga.production_media import validate_media
from manga.voice_service import VoiceService
from manga.voice_models import voice_settings
from manga.voice_timeline import save_timeline
from manga.video_service import VideoService


class ShortFixtureStory(MockStoryAI):
    """3つの短い発話と21の無音コマ。完成した物語品質を証明しない。"""
    def generate(self,request):
        result = super().generate(request)
        if request['kind'] == 'draft':
            payload = json.loads(result['text'])
            for page in payload['story']['pages']:
                for panel in page['panels']:
                    panel.update(description='光と凪が手紙の約束を確かめるテスト図形。',dialogue='',narration='')
            for panel,name,text in zip(payload['story']['pages'][0]['panels'],('光','凪','光'),('約束。','明日。','また。')):
                panel['dialogue'] = name+'：「'+text+'」'
            result['text'] = json.dumps(payload,ensure_ascii=False)
        return result


def test_image(label):
    image = Image.new('RGB',(640,360),'#24394d')
    draw = ImageDraw.Draw(image)
    draw.text((20,20),'TEST ONLY - NOT FOR PUBLICATION '+label,fill='white')
    for x,color in ((180,'#e8af8a'),(460,'#79bcdd')):
        draw.ellipse((x-32,80,x+32,144),fill=color)
        draw.rectangle((x-25,145,x+25,300),fill=color)
    out = io.BytesIO(); image.save(out,'PNG')
    return out.getvalue()


def prepare(output):
    """すべての作成物は新規ディレクトリ内。既存作品へアクセスしない。"""
    output = Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    directory = output/'data'/'projects'
    project = Project(title='手紙の約束（Phase 5-A検証）',genre='青春',synopsis='図形と無料トーンによる工程連携の検証。',page_count=8)
    ProjectStore(directory).save(project)
    story_ai = StoryAIService(directory,project.id,adapter=ShortFixtureStory())
    art = story_ai.artwork
    people = [art.save_character(dict(name=name,hair_color=hair,clothing='制服',visual_identity=identity),expected_revision=art.load()['revision'])
        for name,hair,identity in (('光','黒','丸い髪飾り'),('凪','茶','青いスカーフ'))]
    story_ai.configure(dict(genre='青春',theme='手紙の約束',mood='温かい',page_count=8,concept_count=3,max_cycles=0,goal_score=85),expected_revision=0)
    job = story_ai.create_job('concepts',expected_revision=story_ai.store.load()['revision'])
    story_ai.run_job(job['id'])
    data = story_ai.store.load()
    story_ai.select_concept(data['concept_batches'][-1]['ideas'][0]['id'],expected_revision=data['revision'])
    job = story_ai.create_job('story',expected_revision=story_ai.store.load()['revision'])
    story_ai.run_job(job['id'])
    data = story_ai.store.load()
    adoption = story_ai.adopt(data['candidates'][-1]['id'],expected_revision=data['revision'],expected_story_id=None,confirmed=True)
    version = story_ai.stories.load_history()[-1]
    story_ai.editorial.finalize(expected_version_id=version['id'],expected_revision=story_ai.editorial.load()['revision'])
    characters = CharacterAIService(directory,project.id)
    images = ImageAIService(directory,project.id)
    def bible(person):
        return characters.save_bible(person['id'],version_id=version['id'],expected_revision=characters.store.load()['revision'],expected_artwork_revision=art.load()['revision'],
            locked_fields=['hair_color','clothing','visual_identity'],aliases=[],exceptions=[],reason='自動テストの確認操作を模擬。人間の外見確認ではない。',confirmed=True)
    def manual(kind,label,**selection):
        plan = images.plan(kind,**selection)
        ticket = json.dumps(receipt(plan)).encode()
        verify_receipt(plan,ticket)
        content = test_image(label)
        job = images.create_job(plan,provider='manual',content=content,filename=label+'_TEST_ONLY.png',expected_revision=images.store.load()['revision'])
        candidate = images.run(job['id'])
        images.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'自作図形。自動テストによる確認操作の模擬。公開不可。',expected_revision=images.store.load()['revision'])
        images.adopt(candidate['id'],expected_revision=images.store.load()['revision'],expected_artwork_revision=art.load()['revision'],confirmed=True)
        return candidate
    for person in people:
        bible(person)
        manual('character','reference-'+person['id'],character_id=person['id'])
        bible(person)
    pages = PageStore(directory,project.id)
    settings = pages.load()['settings']; settings['width'] = 600
    pages.save_settings(settings,expected_revision=pages.load()['revision'])
    for page in version['story']['pages']:
        config = default_page(project.id,page,version['id'],settings)
        pages.save_page(page['page'],config,story_version_id=version['id'],expected_revision=pages.load()['revision'])
        for panel in page['panels']:
            art.save_direction(page['page'],panel['panel'],dict(character_ids=[p['id'] for p in people],composition='光：髪色=黒、服装=制服\n凪：髪色=茶、服装=制服'),story_version_id=version['id'],expected_revision=art.load()['revision'])
    for page in version['story']['pages']:
        for panel in page['panels']:
            manual('panel',f"p{page['page']}-c{panel['panel']}",page=page['page'],panel=panel['panel'])
    state = dashboard(project,directory)
    assert [r['state'] for r in state['rows'][:5]] == ['完了']*5
    composition = pages.load()
    artwork = art.load()
    fp = production_fingerprint(version,artwork,composition,art.root)
    studio = StudioStore(directory,project.id)
    for kind,filename in (('png_zip','manga_pages.zip'),('pdf','manga.pdf')):
        content,notes = export_book(version['story'],version['id'],artwork,composition,art,find_japanese_font(),kind)
        studio.save_export(dict(fingerprint=fp,pages=list(range(1,9)),kind=kind,filename=filename),content)
    voice = VoiceService(directory,project.id)
    voice.save_voices({p['id']:voice_settings(dict(voice_id='mock-'+str(i+1))) for i,p in enumerate(people)},expected_revision=0)
    voice.generate([r['id'] for r in voice.plan()['rows']])
    # 再実行は既存の音声を利用。新しいジョブや課金を増やさない。
    before = len(voice.store.load()['jobs'])
    voice.generate([r['id'] for r in voice.plan()['rows']])
    assert len(voice.store.load()['jobs']) == before
    for asset in voice.store.load()['assets']:
        voice.review_audio(asset['id'],expected_revision=voice.store.load()['revision'])
    timeline = save_timeline(voice,expected_revision=voice.store.load()['revision'],gap_ms=0,silent_panel_ms=100)
    service = VideoService(directory,project.id)
    plans = service.plans(timeline)
    for kind,plan in plans.items():
        plan.update(fps=12,quality='fast')
        if kind != 'youtube_main': plan['rows'] = plan['rows'][:3]
        for row in plan['rows']:
            row.update(motion='still',transition='cut',gap_after_ms=0)
    service.save_plans(timeline['id'],plans,expected_revision=0)
    return project,service,timeline,adoption


def verify(output):
    def forbidden(*args,**kwargs): raise AssertionError('実ネットワーク・有料APIは禁止です。')
    with patch.object(socket,'create_connection',forbidden), patch('manga.budget.BudgetedExecutor.run',forbidden), patch('manga.voice_polly.PollyVoice.synthesize',forbidden):
        project,service,timeline,adoption = prepare(output)
        job = service.create_job(timeline['id'],list(service.plans(timeline)),expected_revision=service.store.load()['revision'],acknowledged=True)
        exports = service.run_job(job['id'])
        proof = validate_media(service.directory,project.id)
        assert len(proof['export_ids']) == 3 and not proof['errors']
        from scripts.video_measurements import measure
        measured = {e['kind']:measure(service.store.safe(e['path']),job['plans'][e['kind']],timeline) for e in exports}
        # 再開と同じ操作を行い、動画バイナリが増えないことを検証。
        reused = service.create_job(timeline['id'],list(service.plans(timeline)),expected_revision=service.store.load()['revision'],acknowledged=True)
        assert reused['cache_reused'] and len(service.store.load()['exports']) == 3
        # キャッシュ再利用イベントも元データ更新として扱い、証跡を再計算する。
        proof = validate_media(service.directory,project.id)
        state = dashboard(project,service.directory)
        assert state['rows'][5]['state'] == state['rows'][7]['state'] == '完了'
        assert state['rows'][6]['state'] == state['rows'][8]['state'] == '要確認'
        report = dict(project_id=project.id,project_directory=str(service.store.root),pages=8,panels=24,
            adopted_story_version_id=adoption['story_version_id'],manual_candidates=len(service.voice.artwork.load()['panels']),
            external_api_calls=0,api_cost_jpy=0,test_images=True,mock_audio=True,publication_ready=False,
            stages=[{k:r[k] for k in ('key','state','done','total','missing')} for r in state['rows']],
            synchronization=measured,outputs=[dict(kind=e['kind'],path=str(service.store.safe(e['path'])),metadata=e['metadata']) for e in exports],
            human_review_required=['人物の外見・画像品質','ページの読み順・セリフ配置','字幕の内容','日本語の発音・演技','映像の見切れ・編集・音声同期','素材権利と公開ポリシー'],
            automatic_review_is_simulated=True)
        (Path(output)/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.output),ensure_ascii=False,indent=2))
