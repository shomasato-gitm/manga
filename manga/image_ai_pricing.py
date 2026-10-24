"""表示専用の公式調査カード。実行用PriceQuoteではなく、課金APIを有効化しない。"""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path


def research():
    path=Path(__file__).resolve().parents[1]/'examples'/'image_ai'/'provider_research_20261024.json'
    raw=json.loads(path.read_text(encoding='utf-8'))
    if raw['schema_version']!=1 or raw['format']!='manga-image-provider-research' or not isinstance(raw['cards'],list): raise ValueError('画像APIの調査カードが不正です。')
    age=(date.today()-date.fromisoformat(raw['verified_on'])).days
    return raw,not 0<=age<=30


def output_reference(card,count,fx='160',tax='1.10'):
    """Vertexの1120 output tokenだけ。総額・上限・Gemini Developer API見積ではない。"""
    if type(count) is not int or not 1<=count<=600: raise ValueError('参考枚数は1〜600枚です。')
    ref=card.get('vertex_global_reference')
    if not ref: return None
    fx,tax=Decimal(str(fx)),Decimal(str(tax))
    if not fx.is_finite() or not tax.is_finite() or not 1<=fx<=10000 or not 1<=tax<=2: raise ValueError('為替・税の仮定を確認してください。')
    return Decimal(str(ref['output_tokens_per_1k_image']))*Decimal(ref['image_output_usd_per_million'])/1000000*count*fx*tax
