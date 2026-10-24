#!/usr/bin/env python3
"""完全新規の8ページ24コマを作り、実MP4を3形式へ出力する。APIなし。"""
import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from PIL import Image,ImageDraw
from manga.models import Project
from manga.storage import ProjectStore
from manga.prompts import story_template
from manga.story_storage import StoryStore
from manga.artwork_storage import ArtworkStore
from manga.voice_models import narrator_id,voice_settings
from manga.voice_service import VoiceService
from manga.voice_timeline import save_timeline
from manga.video_service import VideoService


def fixture(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    directory=output/'data'/'projects'
    project=Project(title='君が忘れた、最後の約束（動画検証用）',genre='短尺fixture',synopsis='図形と確認用トーンだけの8ページ24コマ。実ユーザーの画像は使わない。')
    ProjectStore(directory).save(project)
    art=ArtworkStore(directory,project.id)
    cast=[art.save_character({'name':name},expected_revision=art.load()['revision']) for name in ('悠真','美咲','蓮')]
    story=story_template(project)
    for p in story['pages']:
        for panel in p['panels']:panel.update(description='テスト用の人物図形。',dialogue='',narration='')
    story['pages'][0]['panels'][0].update(narration='約束。',dialogue='悠真：「美咲。」')
    story['pages'][0]['panels'][1]['dialogue']='美咲：「転校。」'
    story['pages'][0]['panels'][2]['dialogue']='蓮：「明日。」'
    version=StoryStore(directory,project.id).save(story,expected_version_id=None)
    for p in story['pages']:
        for panel in p['panels']:
            image=Image.new('RGB',(640,360),(40+p['page']*12,75,120+panel['panel']*15));draw=ImageDraw.Draw(image)
            # Head/body near either edge and the middle, including a marked important area.
            for x in (70,320,570):
                draw.ellipse((x-30,45,x+30,105),fill='#ffe0aa');draw.rectangle((x-25,106,x+25,260),fill='#b04070')
            buffer=io.BytesIO();image.save(buffer,format='PNG')
            item=art.add_panel_image(p['page'],panel['panel'],buffer.getvalue(),'fixture.png',story_version_id=version['id'],expected_revision=art.load()['revision'])
            art.select_panel_image(p['page'],panel['panel'],item['id'],expected_revision=art.load()['revision'])
    voice=VoiceService(directory,project.id)
    voices={c['id']:voice_settings({'voice_id':f'mock-{i+1}'}) for i,c in enumerate(cast)}
    voices[narrator_id(project.id)]=voice_settings({'voice_id':'mock-4'})
    voice.save_voices(voices,expected_revision=0);voice.generate([r['id'] for r in voice.plan()['rows']])
    timeline=save_timeline(voice,expected_revision=voice.store.load()['revision'],gap_ms=0,silent_panel_ms=100)
    service=VideoService(directory,project.id);plans=service.plans(timeline)
    for kind,plan in plans.items():
        plan.update(fps=12,quality='fast')
        if kind!='youtube_main':
            plan['rows']=plan['rows'][:3]
            for r in plan['rows']:r['gap_after_ms']=0
        if kind=='youtube_shorts':
            plan['rows']=[plan['rows'][2],plan['rows'][0],plan['rows'][1]];plan['hook']='最後の約束'
    service.save_plans(timeline['id'],plans,expected_revision=0)
    return service,timeline


def verify(output):
    service,timeline=fixture(output)
    job=service.create_job(timeline['id'],list(service.plans(timeline)),expected_revision=service.store.load()['revision'],acknowledged=True)
    exports=service.run_job(job['id'])
    from scripts.video_measurements import measure
    measurements={e['kind']:measure(service.store.safe(e['path']),job['plans'][e['kind']],timeline) for e in exports}
    report=dict(project_id=service.project_id,project_directory=str(service.store.root),pages=8,panels=24,
        paid_api_calls=0,api_cost_jpy=0,test_tones=True,synchronization=measurements,
        outputs=[dict(kind=e['kind'],path=str(service.store.safe(e['path'])),**e['metadata']) for e in exports])
    (Path(output)/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:print(json.dumps(verify(args.output),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as error:parser.exit(1,f'動画検証が完了しませんでした：{error}\n')
