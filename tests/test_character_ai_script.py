import json
import subprocess
import sys
from pathlib import Path

from character_ai_fixtures import hashes


def test_verification_script_uses_new_fixture_and_refuses_existing_output(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path/'character-demo'
    command = [sys.executable, str(root/'scripts'/'verify_character_ai.py'), '--output', str(output)]
    run = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    report = json.loads((output/'report.json').read_text())
    assert (report['pages'], report['panels'], report['extracted_candidates'], report['registered_characters']) == (8,24,3,3)
    assert report['external_api_calls'] == report['api_cost_jpy'] == 0
    assert report['detected_fixed_conflicts'] > 0 and report['remaining_fixed_conflicts'] == 0
    assert report['existing_uuid_preserved'] and report['existing_primary_reference_preserved']
    assert report['prompt_versions'] == 5
    before = hashes(output)
    again = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
    assert again.returncode == 1 and hashes(output) == before
