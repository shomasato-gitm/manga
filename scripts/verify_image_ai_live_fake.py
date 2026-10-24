#!/usr/bin/env python3
"""新規fixture限定。ネットワーク0・実費0の1枚生成→明示レビュー→採用を検証。"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from manga.image_ai_live_provider import GeminiImageAdapter
from manga.image_ai_live_samples import FakeImageHTTP,fake_card
from manga.image_ai_models import REVIEW_FIELDS
from manga.image_ai_samples import create_image_example
from manga.image_ai_service import ImageAIService
from manga.budget.config import open_budget


def verify(output):
    output=Path(output).absolute()
    if output.is_symlink(): raise ValueError('検証先はリンクを使わない新しいフォルダにしてください。')
    for parent in output.parents:
        if parent.is_symlink(): raise ValueError('検証先の親フォルダにリンクを使用できません。')
    output.mkdir(parents=True,exist_ok=False)
    attempts=[]
    def forbidden(*a,**kw): attempts.append(True); raise AssertionError('Network is forbidden in this verification')
    directory=output/'data'/'projects'
    environment=dict(MANGA_DATA_DIR=str(directory),MANGA_BUDGET_DB=str(output/'runtime'/'budget.sqlite3'),GEMINI_API_KEY='',OPENAI_API_KEY='',MANGA_ENABLE_IMAGE_API='0')
    with patch.dict(os.environ,environment),patch('socket.create_connection',forbidden),patch('socket.socket.connect',forbidden):
        project,offline,characters=create_image_example(directory)
        baseline={str(p.relative_to(offline.store.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in offline.store.root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
        plan=offline.plan('panel',page=1,panel=1); http=FakeImageHTTP(plan)
        service=ImageAIService(directory,project.id,live_adapter=GeminiImageAdapter(fake_transport=http))
        card=fake_card(); consents=[r['id'] for r in plan['reference_images']]
        quote=service.estimate_live(plan,card,consents)
        job=service.create_live_job(plan,card,consents,approval_token=quote['approval_token'],confirmed=True,expected_revision=0)
        candidate=service.run(job['id'],approved=True)
        assert not service.store.load()['reviews'] and not service.store.load()['adoptions']
        assert all(hashlib.sha256((service.store.root/name).read_bytes()).hexdigest()==sha for name,sha in baseline.items())
        # このfixtureだけの明示確認。アプリではユーザー自身の目視・権利確認が必須。
        service.review(candidate['id'],{k:True for k in REVIEW_FIELDS},'専用の自作テスト図形。採用操作を検証する明示承認。人物品質の実証ではない。',expected_revision=service.store.load()['revision'])
        adopted=service.adopt(candidate['id'],expected_revision=service.store.load()['revision'],expected_artwork_revision=service.artwork.load()['revision'],confirmed=True)
        service.recover(job['id'],confirmed=True)
        assert len(http.calls)==1 and len(service.store.load()['candidates'])==1 and not attempts
        assert all(hashlib.sha256((service.store.root/name).read_bytes()).hexdigest()==sha for name,sha in baseline.items() if name!='artwork.json')
        stored=service.store.load()['jobs'][0]
        report=dict(real_api_calls=0,actual_api_cost_jpy='0',network_attempts=len(attempts),fake_http_calls=len(http.calls),
                    project_id=project.id,request_id=job['id'],response_id=stored['response_id'],model=stored['returned_model'],
                    usage=stored['usage'],fake_max_reservation_jpy=quote['max_jpy'],fake_converted_charge_jpy=stored['cost_jpy'],
                    calculation='((100 * 0.5 + 1120 * 30) / 1000000) * 160 * 1.10 = 5.922400 JPY (架空料金)',
                    candidate_id=candidate['id'],image_sha256=candidate['sha256'],artwork_image_id=adopted['image_id'],
                    input_sha256=stored['live']['input_sha256'],candidate_count=1,explicit_fixture_adoption=True,
                    existing_files_preserved=True,production_send_enabled=False)
        assert open_budget(directory).summary(project.id)['project_settled_jpy']>0  # 架空の計算が同じledgerを通る。
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try: print(json.dumps(verify(args.output),ensure_ascii=False,indent=2))
    except (ValueError,OSError): parser.exit(1,'新規の空でない未使用フォルダを指定し、失敗時は既存検証結果を削除せず別の保存先を選んでください。\n')
