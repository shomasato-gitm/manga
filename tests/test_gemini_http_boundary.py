"""本番HTTPS処理の境界を完全fake化。外部接続はfixtureでも禁止する。"""
import io
import json
import urllib.error
import urllib.request

import pytest

from manga.budget import BudgetedExecutor
from manga.budget.config import open_budget
from manga.image_ai_live_plan import prepare
from manga.image_ai_live_pricing import actual_charge,bounds,price_quote
from manga.image_ai_live_provider import GeminiImageAdapter,LiveError
from image_ai_fixtures import image_workspace
from test_image_ai_live import setup_live


@pytest.mark.parametrize('outcome',['success','timeout','http429','http500','invalid_json'])
def test_https_boundary_headers_no_retry_bounded_read_and_budget_retention(image_workspace,monkeypatch,capsys,outcome):
    values=setup_live(image_workspace); service,plan,card,consents,http=values
    _,body=prepare(service,plan,card,consents)
    secret='PRIVATE_SENTINEL_HTTP_HEADER_ONLY'
    monkeypatch.setenv('GEMINI_API_KEY',secret)
    # HTTP以外の根拠チェックだけをテスト境界で置換。実装の承認状態は変更しない。
    monkeypatch.setattr('manga.image_ai_live_provider.network_allowed',lambda:None)
    calls=[]; reads=[]; installed=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,maximum):
            reads.append(maximum)
            if outcome=='invalid_json': return b'{bad json'
            raw=dict(http.response,headers={'x-goog-api-key':secret},error={'message':secret})
            return json.dumps(raw).encode()
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert timeout==90
            assert request.full_url=='https://generativelanguage.googleapis.com/v1beta/models/'+card['model']+':generateContent'
            assert '?' not in request.full_url and request.get_method()=='POST'
            assert request.get_header('X-goog-api-key')==secret
            assert secret.encode() not in request.data
            assert json.loads(request.data)['generationConfig']['candidateCount']==1
            if outcome=='timeout': raise TimeoutError(secret)
            if outcome.startswith('http'): raise urllib.error.HTTPError(request.full_url,int(outcome[4:]),secret,{},io.BytesIO(secret.encode()))
            return Response()
    def build(*handlers):
        installed.extend(handlers)
        return Opener()
    monkeypatch.setattr(urllib.request,'build_opener',build)
    ledger=open_budget(service.directory); adapter=GeminiImageAdapter()
    options=dict(project_id=service.project_id,request_id='fake-https-'+outcome,provider='gemini-live',model=card['model'],kind='image',
                 price_quote=price_quote(card),usage_limits=bounds(card),operation=lambda:adapter.send(card,body),actual_charge_jpy=lambda response:actual_charge(card,response))
    if outcome=='success':
        response=BudgetedExecutor(ledger).run(**options)
        assert secret not in json.dumps(response) and ledger.request_status(options['request_id'])['state']=='settled'
    else:
        with pytest.raises(LiveError) as error: BudgetedExecutor(ledger).run(**options)
        assert secret not in str(error.value) and ledger.request_status(options['request_id'])['state']=='uncertain'
    assert len(calls)==1
    assert any(isinstance(h,urllib.request.ProxyHandler) and h.proxies=={} for h in installed)
    redirect=next(h for h in installed if isinstance(h,urllib.request.HTTPRedirectHandler))
    assert redirect.redirect_request(None,None,None,None,None,None) is None
    if reads:
        from manga.image_ai_live_provider import MAX_RESPONSE
        assert reads==[MAX_RESPONSE+1]
    output=capsys.readouterr(); assert secret not in output.out+output.err
