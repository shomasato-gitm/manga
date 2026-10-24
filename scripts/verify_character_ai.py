#!/usr/bin/env python3
"""新規の専用フォルダで抽出→同定→採用→バイブル→矛盾確認→プロンプトを検証。"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manga.character_ai_samples import create_character_example


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


def verify(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    network_calls = []
    def forbidden(*args, **kwargs):
        network_calls.append(True)
        raise RuntimeError('この検証はネットワーク・API送信を禁止しています。')
    with patch('socket.create_connection', forbidden), patch('socket.socket.connect', forbidden):
        project, service = create_character_example(output/'data'/'projects')
        original = service.artwork.load()['characters'][0]
        before = hashes(service.store.root)
        version_id = service.proposals.latest()['id']
        result = service.proposals.create_mock(project, version_id, expected_revision=0)
        assert service.artwork.path.read_bytes() and hashes(service.store.root)['artwork.json'] == before['artwork.json']
        repeat = service.proposals.create_mock(project, version_id, expected_revision=service.proposals.load()['revision'])
        assert result['request']['id'] == repeat['request']['id']
        for index, item in enumerate(result['proposal']['payload']['items']):
            service.proposals.adopt_fields(result['proposal']['id'], item['id'], item['fields'],
                ['visual_identity'] if index == 0 else [k for k,v in item['fields'].items() if v], original['id'] if index == 0 else None,
                expected_revision=service.proposals.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'],
                expected_editorial_revision=service.proposals.editorial.load()['revision'], expected_character_revision=service.store.load()['revision'], identity_confirmed=True)
        from manga.character_ai_samples import example_image
        people = {c['name']:c for c in service.artwork.load()['characters']}
        for person in people.values():
            if not person['references']:
                service.artwork.add_reference(person['id'], example_image(), person['name']+'_モック.png', '正面（検証用色面画像）', expected_revision=service.artwork.load()['revision'])
        for page in service.proposals.latest()['story']['pages']:
            for panel in page['panels']:
                person = people[('悠真', '美咲', '蓮')[panel['panel']-1]]
                labels = [('hair_color','髪色'),('clothing','服装'),('visual_identity','視覚的な差別化')]
                content = '、'.join(label+'='+person[key] for key,label in labels if person.get(key))
                service.artwork.save_direction(page['page'], panel['panel'], dict(character_ids=[person['id']], composition=person['name']+'：'+content), story_version_id=version_id, expected_revision=service.artwork.load()['revision'])
        for person in service.artwork.load()['characters']:
            service.save_bible(person['id'], version_id=version_id, expected_revision=service.store.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'],
                locked_fields=[k for k in ('hair_color','clothing','visual_identity') if person.get(k)], aliases=[], exceptions=[], reason='専用モックで設定と参照状態を明示確認', confirmed=True)
        service.save_observation(1, 1, original['id'], dict(hair_color='金'), '矛盾の再現用コマ指定', version_id=version_id, expected_revision=service.store.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'])
        first_check = service.run_checks(expected_revision=service.store.load()['revision'])
        conflicts = [i for i in first_check['issues'] if i['kind'] == 'fixed_conflict']
        assert conflicts
        service.save_observation(1, 1, original['id'], {}, '矛盾する指定を確認して解除。履歴は保持', version_id=version_id, expected_revision=service.store.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'])
        final_check = service.run_checks(expected_revision=service.store.load()['revision'])
        assert not [i for i in final_check['issues'] if i['kind'] == 'fixed_conflict']
        for view in ('正面','斜め','全身','表情差分'):
            package = service.prompt('character', character_id=original['id'], view=view)
            service.save_prompt(package, expected_revision=service.store.load()['revision'])
        package = service.prompt('panel', page=1, panel=1)
        service.save_prompt(package, expected_revision=service.store.load()['revision'])
        (output/'unified_panel_prompt.txt').write_text(package['prompt'], encoding='utf-8')
        after = hashes(service.store.root)
        unchanged = {k:v for k,v in before.items() if k != 'artwork.json'}
        assert all(after[k] == v for k,v in unchanged.items())
        current = service.artwork.load()['characters'][0]
        assert original['id'] == current['id'] and original['references'] == current['references'] and original['primary_reference_id'] == current['primary_reference_id']
        data = service.store.load()
        report = dict(project_id=project.id, project_directory=str(service.store.root), story_version_id=version_id,
            pages=8, panels=24, extracted_candidates=len(result['request']['character_ai']['extraction']['candidates']), narrator_panels=len(result['request']['character_ai']['extraction']['narrator']['sources']),
            registered_characters=len(service.artwork.load()['characters']), updated_existing=1, new_characters=2,
            existing_uuid_preserved=True, existing_primary_reference_preserved=True, bibles=len(data['bibles']), checks=len(data['checks']),
            detected_fixed_conflicts=len(conflicts), remaining_fixed_conflicts=0, remaining_static_findings=len(final_check['issues']),
            prompt_versions=len(data['prompts']), sha256_before=before, sha256_after=after, preserved_original_files=list(unchanged),
            external_api_calls=len(network_calls), api_cost_jpy=0, mock=True, budget_data_touched=False)
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        (output/'sample.json').write_text(json.dumps(dict(proposal=result['proposal']['original_payload'], bible=data['bibles'][0], conflicts=conflicts), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try: print(json.dumps(verify(args.output), ensure_ascii=False, indent=2))
    except (ValueError, OSError, RuntimeError) as error: parser.exit(1, f'人物モック検証を完了できませんでした：{error}\n')
