"""正の架空見積・実台帳を使うHTTPテスト用境界。実通信なし。"""
from uuid import uuid4
from manga.budget import BudgetLedger,BudgetedExecutor
from manga.story_ai_pricing import quote,limits
from story_ai_fixtures import fake_card


def guarded(tmp_path,operation,*,provider='openai-responses',request_id=None,card=None):
    if provider=='aws-polly':
        from voice_fixtures import fake_card as voice_card
        from manga.voice_pricing import price_quote,usage_limits
        price=price_quote(voice_card(),'neural');bounds=usage_limits('<speak>確認</speak>');model='neural'
    else:
        card=card or fake_card();price=quote(card);bounds=limits(card);model=card['model']
    return BudgetedExecutor(BudgetLedger(tmp_path/'adapter-budget.sqlite3')).run(project_id=str(uuid4()),
        request_id=request_id or str(uuid4()),provider=provider,model=model,kind='test',operation=operation,
        price_quote=price,usage_limits=bounds,actual_charge_jpy=lambda _:'.01')
