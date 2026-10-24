import json
import subprocess
import sys
from pathlib import Path

from image_ai_fixtures import hashes


def test_mock_manual_artwork_voice_video_handoff_and_no_overwrite(tmp_path):
    root=Path(__file__).resolve().parents[1]; output=tmp_path/'new-image-demo'
    command=[sys.executable,str(root/'scripts'/'verify_image_ai.py'),'--output',str(output)]
    result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stderr
    report=json.loads((output/'report.json').read_text())
    assert (report['pages'],report['panels'],report['characters'],report['partial_selected_panels'],report['final_selected_panels'])==(8,24,3,3,24)
    assert report['mock_candidates']==28 and report['manual_candidates']==1 and report['reference_adoptions']==3
    assert report['video_source_images']==24 and report['voice_utterances']==32
    assert report['external_api_calls']==report['api_cost_jpy']==report['new_budget_reservations_jpy']==0
    before=hashes(output); repeated=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=20)
    assert repeated.returncode==1 and hashes(output)==before
