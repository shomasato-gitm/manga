"""画面共通の入力保持・保存状態・処理時間。ブラウザセッション内だけに下書きを保持する。"""

import copy
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar

import streamlit as native

_SCOPE = ContextVar("manga_ui_scope", default="global")
_FORM = ContextVar("manga_ui_form", default="")
_EXPANDER = ContextVar("manga_ui_expander", default=0)
INPUTS = {"text_input", "text_area", "number_input", "checkbox", "selectbox", "multiselect", "radio", "slider", "color_picker"}


@contextmanager
def scope(name):
    token = _SCOPE.set(name)
    try:
        yield
    finally:
        _SCOPE.reset(token)


class UI:
    def __init__(self, target=native, kind=None):
        self.target = target
        self.kind = kind

    def __enter__(self):
        self._context_token = _EXPANDER.set(_EXPANDER.get()+1) if self.kind == "expander" else None
        self.target.__enter__()
        return self

    def __exit__(self, *args):
        try: return self.target.__exit__(*args)
        finally:
            if self._context_token is not None: _EXPANDER.reset(self._context_token)

    def __getattr__(self, name):
        if name == "sidebar":
            return UI(self.target.sidebar)
        if name == "button":
            return self.button
        if name == "form":
            return self.form
        if name == "form_submit_button":
            return self.submit
        function = getattr(self.target, name)
        if name in ('json','caption'):
            def present(*args,**kwargs):
                import re
                standard=native.session_state.get('ux_mode','標準モード')=='標準モード'
                text=str(args[0]) if args else ''
                technical=name=='json' or bool(re.search(r'[0-9a-f]{8}-[0-9a-f-]{27,}|SHA-?256|[0-9a-f]{64}',text))
                warning=any(word in text for word in ('権利','課金','予算','未確認','警告','未確定','要確認'))
                if standard and technical and not warning and not _EXPANDER.get():
                    with self.target.expander('内部データ・技術情報（詳細）'):
                        return function(*args,**kwargs)
                return function(*args,**kwargs)
            return present
        if name in INPUTS:
            return lambda *args, **kwargs: self.input(name, function, *args, **kwargs)
        if name in ("container", "empty", "expander"):
            return lambda *args, **kwargs: UI(function(*args, **kwargs), kind=name)
        if name in ("columns", "tabs"):
            return lambda *args, **kwargs: [UI(item) for item in function(*args, **kwargs)]
        return function

    @contextmanager
    def form(self, key, **kwargs):
        # ネイティブformは未送信の文字をメニュー移動時に失うため、保存ボタン付きの通常入力にする。
        token = _FORM.set(key)
        try:
            with self.target.container():
                yield self
        finally:
            _FORM.reset(token)

    def submit(self, label, **kwargs):
        kwargs.pop("enter_to_submit", None)
        kwargs.setdefault("key", f"submit_{_SCOPE.get()}_{_FORM.get()}_{label}")
        return self.button(label, **kwargs)

    def button(self,label,**kwargs):
        key=kwargs.pop("key",f"button_{_SCOPE.get()}_{label}")
        kwargs["key"]=f"{key}_epoch_{native.session_state.get('_action_epoch',0)}"
        kwargs["disabled"]=kwargs.get("disabled",False) or native.session_state.get("_operation_busy",False)
        if 'help' not in kwargs and any(v in label for v in ('保存','生成','採用','確認','実行','復元','準備')):
            kwargs['help']='先に確認項目を承認してください。素材や版の不足理由は、この画面の案内・警告で確認できます。' if any(v in label for v in ('採用','確認','実行','生成')) else '必要な入力・変更内容を確認してください。未保存の入力は保持されます。'
        return self.target.button(label,**kwargs)

    def input(self, name, function, *args, **kwargs):
        drafts = native.session_state.setdefault("_input_drafts", {})
        explicit = kwargs.get("key")
        if explicit is None:
            defaults = {k: kwargs[k] for k in ("value", "index", "min_value", "max_value") if k in kwargs}
            digest = hashlib.sha256(json.dumps(defaults, ensure_ascii=False, default=str, sort_keys=True).encode()).hexdigest()[:10]
            explicit = f"draft_{_SCOPE.get()}_{_FORM.get()}_{args[0] if args else kwargs.get('label')}_{digest}"
            kwargs["key"] = explicit
        memory_key = (_SCOPE.get(), str(explicit))
        if explicit not in native.session_state and memory_key in drafts:
            value = copy.deepcopy(drafts[memory_key])
            options = args[1] if len(args) > 1 else kwargs.get("options")
            valid = True
            if name in ("selectbox", "radio") and options is not None:
                valid = value in options
            if name == "multiselect" and options is not None:
                valid = all(item in options for item in value)
            if name == "number_input":
                valid = (kwargs.get("min_value") is None or value >= kwargs["min_value"]) and (kwargs.get("max_value") is None or value <= kwargs["max_value"])
            if valid:
                native.session_state[explicit] = value
        result = function(*args, **kwargs)
        drafts[memory_key] = copy.deepcopy(result)
        return result


ui = UI()


@contextmanager
def operation(label, *, invalidate_actions=False):
    state = native.session_state
    state["_operation_busy"] = True
    started = time.perf_counter()
    status = native.empty()
    status.info(f"処理中：{label}")
    try:
        with native.spinner(label):
            yield
    except Exception as error:
        status.error(f"エラー：{error}。内容・保存先・最新の版を確認して再試行してください。")
        raise
    else:
        status.success(f"完了：{label}")
        if invalidate_actions:
            state["_action_epoch"]=state.get("_action_epoch",0)+1
    finally:
        state["_operation_busy"] = False
        log = state.setdefault("_timings", [])
        log.append(dict(screen=_SCOPE.get(), operation=label, ms=round((time.perf_counter()-started)*1000, 2)))
        del log[:-100]


def record_screen(name, elapsed):
    log = native.session_state.setdefault("_screen_timings", [])
    log.append(dict(screen=name, ms=round(elapsed*1000, 2)))
    del log[:-100]
