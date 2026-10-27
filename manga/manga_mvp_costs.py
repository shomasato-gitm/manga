"""公式調査の比較表示だけ。実行用料金カード・予算・ネットワーク設定に流用しない。"""
import json
from decimal import Decimal
from pathlib import Path


def image_comparison(panels=24, references=2):
    if type(panels) is not int or type(references) is not int or not 1 <= panels <= 600 or not 0 <= references <= 50:
        raise ValueError('比較する画像件数が不正です。')
    path = Path(__file__).resolve().parents[1] / 'docs' / 'manga-mvp-image-providers.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['network_enabled'] is not False or any(p['maximum_charge_verified'] is not False for p in data['providers']):
        raise ValueError('この調査記録はAPIの送信許可には使えません。')
    result = []
    for row in data['providers']:
        price = Decimal(row['output_usd_per_image']) if row['output_usd_per_image'] is not None else None
        result.append(dict(model=row['model'], per_image_usd=str(price) if price is not None else '未確認',
            panels_usd=str(price * panels) if price is not None else '未確認',
            total_usd=str(price * (panels + references)) if price is not None else '未確認',
            setting=row['setting'], checked_on=data['checked_on'], network_enabled=False))
    return result
