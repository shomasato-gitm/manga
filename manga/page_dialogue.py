"""明確な話者ラベルだけを分離する。曖昧な入力は原文を保持して確認へ回す。"""
import re
from dataclasses import dataclass

SPEAKER = re.compile(r"(?m)^[ \t]*([^\n:：「」『』]{1,40})[：:][ \t]*")


@dataclass(frozen=True)
class Utterance:
    speaker: str
    text: str
    raw: str
    needs_review: bool = False


def split_dialogue(text):
    if not text.strip():
        return []
    matches = list(SPEAKER.finditer(text))
    if not matches or text[:matches[0].start()].strip():
        return [Utterance("", text, text, True)]
    result = []
    for index, match in enumerate(matches):
        end = matches[index+1].start() if index+1 < len(matches) else len(text)
        raw = text[0 if index == 0 else match.start():end]
        body = text[match.end():end].strip()
        # 閉じていない引用や、同じ行に混在する話者は推測で分割しない。
        if not match[1].strip() or not body or re.search(r"[」』]\s*[^\n:：]{1,40}[：:]", body):
            return [Utterance("", text, text, True)]
        if body[0] in "「『":
            closing = {"「":"」", "『":"』"}[body[0]]
            if not body.endswith(closing) or body.count(body[0]) != body.count(closing):
                return [Utterance("", text, text, True)]
            body = body[1:-1]
        result.append(Utterance(match[1].strip(), body, raw))
    return result
