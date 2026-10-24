"""課金ゼロのfake。架空の上限・料金であり実APIの根拠には使用しない。"""
import base64
import copy
import json
from datetime import date

from manga.image_ai_provider import mock_image


def fake_card():
    return dict(provider='gemini-live',model='fake-image-contract-v1',returned_models=['fake-image-contract-v1'],
                verified_on=date.today().isoformat(),source_url='https://ai.google.dev/gemini-api/docs/pricing',
                protocol_url='https://ai.google.dev/api/generate-content',terms_url='https://ai.google.dev/gemini-api/terms',
                tax_note='FAKE ONLY: 架空の単価・最大量。実課金の根拠ではない。',
                input_per_million_usd='0.5',output_per_million_usd='30',fx_jpy_per_usd='160',tax_multiplier='1.10',safety_multiplier='1.25',
                max_input_tokens=10000,max_output_tokens=2000,max_thinking_tokens=0,max_reference_images=4,max_reference_bytes=2*1024*1024,
                maximum_charge_verified=True,terms_verified=True,image_size='1K',aspect_ratio='1:1',width=1024,height=1024)


class FakeImageHTTP:
    """注入専用。認証情報・環境変数・ソケットを参照しない。"""
    def __init__(self,plan):
        self.calls=[]
        image=mock_image(dict(id='FAKE_HTTP',plan=plan))
        self.response=dict(modelVersion='fake-image-contract-v1',responseId='fake-response-1',
            usageMetadata=dict(promptTokenCount=100,candidatesTokenCount=1120,thoughtsTokenCount=0,totalTokenCount=1220),
            candidates=[dict(finishReason='STOP',content=dict(parts=[dict(inlineData=dict(mimeType='image/png',data=base64.b64encode(image).decode()))]))])
        self.error=None

    def __call__(self,model,body):
        request=json.loads(body)
        assert request['generationConfig']['candidateCount']==1
        assert set(request)=={'contents','generationConfig'}
        self.calls.append(dict(model=model,body=request))
        if self.error: raise self.error
        return copy.deepcopy(self.response)
