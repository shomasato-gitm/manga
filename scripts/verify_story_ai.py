#!/usr/bin/env python3
"""APIなしで企画3〜5案→5回改善→比較→明示採用を新規作品で確認する。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from manga.models import Project
from manga.storage import ProjectStore
from manga.artwork_storage import ArtworkStore
from manga.story_ai_service import StoryAIService


def verify(output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    directory=output/'data'/'projects'
    project=Project(title='君が忘れた、最後の約束（企画・脚本モック）',genre='青春',synopsis='別れの前に約束を伝える8ページの1話完結作品。')
    ProjectStore(directory).save(project)
    art=ArtworkStore(directory,project.id)
    for name in ('悠真','美咲'):art.save_character({'name':name},expected_revision=art.load()['revision'])
    service=StoryAIService(directory,project.id)
    service.configure(dict(genre='青春',theme='言えなかった約束',mood='切なく温かな余韻',page_count=8,concept_count=5,max_cycles=5,goal_score=100),expected_revision=0)
    concepts=service.create_job('concepts',expected_revision=service.store.load()['revision']);service.run_job(concepts['id'])
    data=service.store.load();batch=data['concept_batches'][-1]
    service.select_concept(batch['ideas'][0]['id'],expected_revision=data['revision'])
    job=service.create_job('story',expected_revision=service.store.load()['revision']);result=service.run_job(job['id'])
    data=service.store.load();chosen=next(c for c in data['candidates'] if c['id']==result['best_candidate_id'])
    assert len(data['candidates'])==6 and len(data['evaluations'])==6 and result['cycle']==5
    assert service.stories.load_history()==[]
    adoption=service.adopt(chosen['id'],expected_revision=data['revision'],expected_story_id=None,confirmed=True)
    sample=dict(ideas=batch['ideas'],initial_story=data['candidates'][0]['story'],improved_story=chosen['story'],page_hooks=chosen['page_hooks'],evaluation=data['evaluations'][-1]['payload'])
    (output/'sample.json').write_text(json.dumps(sample,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report=dict(project_id=project.id,project_directory=str(service.store.root),concepts=5,pages=8,panels=24,improvement_cycles=result['cycle'],candidate_versions=6,
        evaluations=6,requests=len(data['requests']),stop_reason=result['stop_reason'],adopted_version_id=adoption['story_version_id'],
        story_store_versions=len(service.stories.load_history()),paid_api_calls=0,api_cost_jpy=0,mock=True)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);args=parser.parse_args()
    try:print(json.dumps(verify(args.output),ensure_ascii=False,indent=2))
    except (ValueError,OSError) as error:parser.exit(1,f'企画・脚本の検証を完了できませんでした：{error}\n')
