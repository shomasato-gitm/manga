#!/usr/bin/env python3
"""APIなしの短い音声fixtureを新規フォルダへ作る。既存フォルダは上書きしない。"""
import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from manga.models import Project
from manga.prompts import story_template
from manga.storage import ProjectStore
from manga.artwork_storage import ArtworkStore
from manga.story_storage import StoryStore
from manga.voice_models import narrator_id,voice_settings
from manga.voice_service import VoiceService
from manga.voice_timeline import save_timeline


def verify(output):
    output=Path(output).expanduser().resolve()
    output.mkdir(parents=True,exist_ok=False)
    directory=output/'data'/'projects'
    project=Project(title='君が忘れた、最後の約束（音声fixture）',genre='確認用',
                    synopsis='有料APIなしで、3人物・ナレーター・8ページ24コマの音声データを検証する。')
    ProjectStore(directory).save(project)
    art=ArtworkStore(directory,project.id)
    characters=[art.save_character({'name':name},expected_revision=art.load()['revision']) for name in ('悠真','美咲','蓮')]
    story=story_template(project)
    for page in story['pages']:
        for panel in page['panels']:
            panel.update(description='音声テスト用の無音コマ。実作品の画像は使用しない。',dialogue='',narration='')
    story['pages'][0]['panels'][0].update(description='夕暮れの教室。',narration='卒業まで、あと7日。',
        dialogue='悠真：「あのさ、美咲。」\n美咲：「先に話してもいい？」')
    story['pages'][0]['panels'][1]['dialogue']='蓮：「ふたりとも、どうした？」'
    story['pages'][0]['panels'][2]['dialogue']='悠真：「何でもないよ。」'
    version=StoryStore(directory,project.id).save(story,expected_version_id=None)
    service=VoiceService(directory,project.id)
    voices={c['id']:voice_settings({'voice_id':f'mock-{i+1}'}) for i,c in enumerate(characters)}
    voices[narrator_id(project.id)]=voice_settings({'voice_id':'mock-4'})
    service.save_voices(voices,expected_revision=0)
    ids=[r['id'] for r in service.plan()['rows']]
    first=service.generate(ids)
    cached=service.generate(ids)
    changed=copy.deepcopy(story)
    changed['pages'][0]['panels'][2]['dialogue']='悠真：「本当のことを、話すよ。」'
    service.stories.save(changed,expected_version_id=version['id'])
    revised=service.generate(ids)
    timeline=save_timeline(service,expected_revision=service.store.load()['revision'])
    data=service.store.load()
    assert all(j['settings']['provider']=='mock' and j['ledger_path'] is None for j in data['jobs'])
    assert sum(r['cache_hit'] for r in cached)==5 and sum(r['cache_hit'] for r in revised)==4
    report=dict(project_id=project.id,project_directory=str(service.store.root),pages=8,panels=24,speakers=4,
                first_generated=len(first),repeat_cache_hits=5,changed_line_generated=1,assets_preserved=len(data['assets']),
                paid_provider_calls=0,api_cost_jpy=0,mock_audio=True,duration_ms=timeline['duration_ms'],
                timeline_json=str(service.store.safe_path(data['timelines'][-1]['paths']['json'])),
                subtitles_srt=str(service.store.safe_path(data['timelines'][-1]['paths']['srt'])),
                measurements=sorted({a['info']['measurement'] for a in data['assets']}))
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='まだ存在しない出力先フォルダ')
    args=parser.parse_args()
    try:print(json.dumps(verify(args.output),ensure_ascii=False,indent=2))
    except (OSError,ValueError) as error:parser.exit(1,'fixture作成に失敗しました。既存フォルダ・保存先を確認してください。\n')
