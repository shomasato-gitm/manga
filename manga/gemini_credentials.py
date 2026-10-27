"""Gemini認証の読み取りと通信なし比較。秘密の識別子・ハッシュを作らない。"""
import hmac
import os
from threading import Lock

from manga.story_ai_provider import StoryProviderError


def api_key():
    """従来どおりこの変数だけを使用する。補正・別キーへの切替はしない。"""
    value = os.environ.get('GEMINI_API_KEY', '')
    if not _valid_format(value):
        raise StoryProviderError('GEMINI_API_KEYをMacの環境変数へ設定してください。値は画面・JSONへ入力しないでください。')
    return value


def _valid_format(value):
    return bool(value) and len(value) <= 512 and not any(c.isspace() for c in value)


class _StartupComparison:
    # ブラウザ/session_state/Store/ファイルには渡さない。プロセス終了で破棄する。
    __slots__ = ('__value', 'stage')

    def __init__(self, value, stage):
        self.__value = value
        self.stage = stage

    def matches(self, value):
        return hmac.compare_digest(self.__value.encode('utf-8', 'surrogatepass'), value.encode('utf-8', 'surrogatepass'))

    def __repr__(self):
        return '<Gemini startup comparison: private memory only>'

    def __reduce_ex__(self, protocol):
        raise TypeError('認証の比較用メモリは保存できません。')


_startup = None
_lock = Lock()


def capture_startup(stage='app_first_run'):
    """アプリ初回実行時だけ捕捉。rerun・作品切替では基準を更新しない。"""
    global _startup
    if stage not in ('app_first_run','before_streamlit'):
        raise ValueError('対応する起動段階を指定してください。')
    with _lock:
        if _startup is None:
            _startup = _StartupComparison(os.environ.get('GEMINI_API_KEY', ''),stage)


def credential_report():
    """返すのは固定状態と真偽値だけ。認証成功や読込元ファイルは推測しない。"""
    value = os.environ.get('GEMINI_API_KEY', '')
    alternate = os.environ.get('GOOGLE_API_KEY', '')
    format_ok = _valid_format(value)
    with _lock:
        startup = 'not_recorded' if _startup is None else 'same' if _startup.matches(value) else 'changed'
        stage = 'not_recorded' if _startup is None else _startup.stage
    relation = ('same' if hmac.compare_digest(value.encode('utf-8', 'surrogatepass'), alternate.encode('utf-8', 'surrogatepass')) else 'different') if value and alternate else 'not_comparable'
    return dict(source='GEMINI_API_KEY',key_set=bool(value),format_ok=format_ok,startup_comparison=startup,startup_stage=stage,
        google_key_set=bool(alternate),google_comparison=relation,
        format_hints=dict(whitespace=any(c.isspace() for c in value),too_long=len(value)>512,
            non_ascii=not value.isascii(),control=any(ord(c)<32 or ord(c)==127 for c in value),
            quote=bool(value) and (value[0] in ('"',"'") or value[-1] in ('"',"'")),
            assignment=value.startswith(('GEMINI_API_KEY=','GOOGLE_API_KEY=','export '))),
        authentication='unverified',network_calls=0,budget_reservations=0)


if __name__ == '__main__':
    import json
    capture_startup()
    print(json.dumps(credential_report(),ensure_ascii=False,indent=2))
