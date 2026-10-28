import base64
import copy
import io
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image

from manga.gemini_image_contract import MODEL, FLAGS, template
from manga.image_ai_live_provider import GeminiImageAdapter
from manga.image_ai_service import ImageAIService


def card(**changes):
    result=template()
    result.update(verified_on=datetime.now(ZoneInfo('Asia/Tokyo')).date().isoformat(),fx_jpy_per_usd='150',tax_multiplier='1.10',
        tax_note='TEST ONLY hypothetical FX/tax',**{key:True for key in FLAGS})
    result.update(changes)
    return result


class FakeImage:
    def __init__(self):
        self.calls=[]; self.error=None; self.mutate=None

    def __call__(self, model, body):
        import json
        self.calls.append(json.loads(body))
        if self.error: raise self.error
        stream=io.BytesIO(); Image.new('RGB',(1024,1024),'pink').save(stream,'PNG')
        value=dict(modelVersion=MODEL,responseId='fake-image-result',
            usageMetadata=dict(promptTokenCount=100,candidatesTokenCount=1120,thoughtsTokenCount=25,totalTokenCount=1245,
                promptTokensDetails=[dict(modality='TEXT',tokenCount=100)],candidatesTokensDetails=[dict(modality='IMAGE',tokenCount=1120)]),
            candidates=[dict(finishReason='STOP',content=dict(parts=[dict(inlineData=dict(mimeType='image/png',data=base64.b64encode(stream.getvalue()).decode()))]))])
        if self.mutate: self.mutate(value)
        return value


def service(workspace):
    _, original, _=workspace
    fake=FakeImage()
    result=ImageAIService(original.directory,original.project_id,live_adapter=GeminiImageAdapter(fake_transport=fake))
    return result,fake


def create(service, plan, price=None, scope_id=None):
    price=price or card(); refs=[r['id'] for r in plan['reference_images']]
    quote=service.estimate_live(plan,price,refs)
    job=service.create_live_job(plan,price,refs,approval_token=quote['approval_token'],confirmed=True,
        expected_revision=service.store.load()['revision'],scope_id=scope_id)
    return job,quote
