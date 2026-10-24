import json
import subprocess
import sys
from pathlib import Path


def test_eight_pages_manual_images_png_pdf_voice_and_three_real_mp4(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path/'new-production'
    command = [sys.executable,str(root/'scripts/verify_production.py'),'--output',str(output)]
    result = subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=240)
    assert result.returncode == 0, result.stdout[-1000:]+result.stderr[-3000:]
    report = json.loads((output/'report.json').read_text())
    assert (report['pages'],report['panels'],report['manual_candidates']) == (8,24,24)
    assert report['external_api_calls'] == report['api_cost_jpy'] == 0
    assert len(report['outputs']) == 3 and not report['publication_ready']
    assert report['test_images'] and report['mock_audio'] and report['automatic_review_is_simulated']
    for result in report['synchronization'].values():
        assert result['max_audio_error_ms'] <= 250 and result['max_subtitle_error_ms'] <= 250
    states = {r['key']:r['state'] for r in report['stages']}
    assert states['pages'] == states['video'] == '完了'
    assert states['voice'] == states['quality'] == '要確認'
    before = (output/'report.json').read_bytes()
    repeated = subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=20)
    assert repeated.returncode != 0 and (output/'report.json').read_bytes() == before
