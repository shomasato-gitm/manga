"""確認したテキストモデルだけの設定。プロバイダーや料金JSONを自動で切り替えない。"""
from dataclasses import dataclass
from types import MappingProxyType

LEGACY_MODEL = 'gemini-2.5-flash-lite'
DEFAULT_MODEL = 'gemini-3.5-flash-lite'


@dataclass(frozen=True)
class GeminiModel:
    id: str
    input_limit: int
    output_limit: int
    input_rate: str
    output_rate: str
    thinking_field: str
    thinking_value: object
    verified_on: str
    legacy: bool = False

    @property
    def model_url(self):
        return 'https://ai.google.dev/gemini-api/docs/models/'+self.id

    @property
    def thinking_config(self):
        return {'thinkingBudget':self.thinking_value} if self.thinking_field=='thinking_budget' else {'thinkingLevel':self.thinking_value}

    @property
    def thinking_label(self):
        return '思考OFF' if self.legacy else '思考最小（OFFではありません。思考分も出力上限・費用に含む）'

    def structured_output(self,rule):
        if self.legacy:
            return {'responseMimeType':'application/json','responseJsonSchema':rule}
        return {'responseFormat':{'text':{'mimeType':'APPLICATION_JSON','schema':rule}}}


MODELS = MappingProxyType({
    LEGACY_MODEL: GeminiModel(LEGACY_MODEL,1048576,65536,'0.10','0.40','thinking_budget',0,'2026-10-26',True),
    DEFAULT_MODEL: GeminiModel(DEFAULT_MODEL,1048576,65536,'0.30','2.50','thinking_level','minimal','2026-10-27'),
})


def get_model(identifier):
    if not isinstance(identifier,str) or identifier not in MODELS:
        raise ValueError('対応済みのGeminiモデルを選び、モデルに対応する料金JSONを使用してください。')
    return MODELS[identifier]
