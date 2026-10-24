#!/usr/bin/env python3
"""専用の新規fixtureで基準画像→24コマ→人間確認→Artwork→音声・動画素材を検証。"""
import argparse
import hashlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from PIL import Image,ImageDraw
from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_samples import create_image_example,confirm_bible


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


def verify(output):
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=False)
    attempts=[]
    def no_network(*a,**kw): attempts.append(True); raise RuntimeError('この検証はネットワーク・有料APIを禁止しています。')
    with patch('socket.create_connection',no_network),patch('socket.socket.connect',no_network):
        project,service,characters=create_image_example(output/'data'/'projects')
        old_art=service.artwork.load(); before=hashes(service.store.root)
        def generate(kind='panel',provider='mock',content=None,filename=None,**selection):
            plan=service.plan(kind,**selection)
            job=service.create_job(plan,provider=provider,content=content,filename=filename,new_request=True,expected_revision=service.store.load()['revision'])
            return service.run(job['id'])
        def adopt(candidate):
            service.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'自作の明確なテスト素材。人間による確認操作を模擬。完成画像ではない。',expected_revision=service.store.load()['revision'])
            return service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],confirmed=True)
        references=[]
        for person in old_art['characters']:
            candidate=generate('character',character_id=person['id'])
            references.append(adopt(candidate)); confirm_bible(characters,person['id'])
        after_references=service.artwork.load()
        panels={}
        for page in characters.proposals.latest()['story']['pages']:
            for panel in page['panels']:
                panels[(page['page'],panel['panel'])]=generate(page=page['page'],panel=panel['panel'])
        assert service.artwork.load()==after_references
        panels[(1,1)]=generate(page=1,panel=1)  # 再生成候補。前の候補も残す。
        manual=Image.new('RGB',(1024,1024),'#c8dbdf'); draw=ImageDraw.Draw(manual)
        draw.text((30,30),'MANUAL TEST FIXTURE - NOT AI - NOT FOR PUBLICATION',fill='red')
        stream=io.BytesIO(); manual.save(stream,'PNG')
        panels[(1,2)]=generate(provider='manual',content=stream.getvalue(),filename='手動テスト画像.png',page=1,panel=2)
        selections=[]
        for key in ((1,1),(1,2),(1,3)): selections.append(adopt(panels[key]))
        partial_count=sum(bool(e['selected_image_id']) for e in service.artwork.load()['panels'].values())
        assert partial_count==3
        for key,candidate in panels.items():
            if key[0]!=1: selections.append(adopt(candidate))
        assert len(selections)==24
        from manga.voice_service import VoiceService
        from manga.voice_models import voice_settings,narrator_id
        from manga.voice_timeline import save_timeline
        from manga.voice_contract import validate_timeline
        from manga.video_sources import snapshot
        voice=VoiceService(service.directory,project.id)
        cast=service.artwork.load()['characters']
        voices={p['id']:voice_settings(dict(voice_id='mock-'+str(i+1))) for i,p in enumerate(cast)}
        voices[narrator_id(project.id)]=voice_settings(dict(voice_id='mock-4'))
        voice.save_voices(voices,expected_revision=voice.store.load()['revision'])
        voice.generate([r['id'] for r in voice.plan()['rows']])
        for asset in voice.store.load()['assets']: voice.review_audio(asset['id'],expected_revision=voice.store.load()['revision'])
        timeline=save_timeline(voice,expected_revision=voice.store.load()['revision'],gap_ms=0,silent_panel_ms=100)
        validate_timeline(timeline); video=snapshot(voice,timeline['id'])
        assert len(video['images'])==24 and all(s['image'] for s in timeline['scenes'])
        assert any('テスト用コマ画像' in w for w in timeline['warnings'])
        final=service.artwork.load(); after=hashes(service.store.root)
        preserved={k:v for k,v in before.items() if k not in ('artwork.json','character_ai.json','voice.json')}
        assert all(after[k]==v for k,v in preserved.items())
        assert [c['id'] for c in old_art['characters']]==[c['id'] for c in final['characters']]
        for old,new in zip(old_art['characters'],final['characters']):
            assert all(r in new['references'] for r in old['references'])
        assert all(i in final['panels']['1:1']['candidates'] for i in old_art['panels']['1:1']['candidates'])
        data=service.store.load()
        report=dict(project_id=project.id,project_directory=str(service.store.root),pages=8,panels=24,characters=3,narrator=True,
            jobs=len(data['jobs']),candidates=len(data['candidates']),mock_candidates=sum(c['mock'] for c in data['candidates']),manual_candidates=sum(not c['mock'] for c in data['candidates']),
            reference_adoptions=len(references),panel_adoptions=len(selections),partial_selected_panels=partial_count,final_selected_panels=24,
            existing_character_uuids_preserved=True,existing_reference_ids_preserved=True,existing_candidates_preserved=True,
            voice_timeline_id=timeline['id'],voice_utterances=len(timeline['subtitles']),voice_assets=len(timeline['assets']),video_source_images=len(video['images']),
            publication_ready=False,warnings=timeline['warnings'],sha256_before=before,sha256_after=after,preserved_original_files=list(preserved),
            external_api_calls=len(attempts),api_cost_jpy=0,new_budget_reservations_jpy=0,mock=True)
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (output/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (output/'sample.json').write_text(json.dumps(dict(plan=data['jobs'][-1]['plan'],candidate=data['candidates'][-1],adoption=data['adoptions'][-1]),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',required=True); args=parser.parse_args()
    try: print(json.dumps(verify(args.output),ensure_ascii=False,indent=2))
    except (ValueError,OSError,RuntimeError) as error: parser.exit(1,f'画像制作の検証を完了できませんでした：{error}\n')
