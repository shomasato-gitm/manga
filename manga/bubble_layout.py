"""文字量に基づく控えめな初期配置。顔検出はせず、右・左の縁から配置する。"""
import math
import unicodedata
from uuid import UUID, uuid5
from manga.page_dialogue import split_dialogue, Utterance


def body_geometry(bubble, width, height):
    body_h = max(1, round(height*(.84 if bubble["tail"] else 1)))
    ratio = .18 if bubble["shape"] == "ellipse" else .10
    inset_x, inset_y = max(2,round(width*ratio)), max(2,round(body_h*ratio))
    return body_h, inset_x, inset_y


def size_for_text(text, flow, shape, tail, font_scale, frame, page_width, count=1):
    """改行・全角/半角も考慮した初期値。実際のフォントの収まりは描画時に検証する。"""
    unit = page_width*font_scale*1.27
    paragraphs = text.replace("\r\n","\n").split("\n")
    lengths = [sum(0.6 if unicodedata.east_asian_width(c) in ("Na","H") else 1 for c in line) for line in paragraphs]
    candidates = []
    padding = .64 if shape == "ellipse" else .80
    max_w = min(.86 if shape == "rectangle" else .62, .44 if count > 1 else 1)
    max_h = .32 if shape == "rectangle" else .84
    for capacity in range(5, 23):
        lines = sum(max(1, math.ceil(n/capacity)) for n in lengths)
        occupied = min(capacity,max(lengths,default=1))
        if flow == "vertical":
            w, h = lines*unit/padding+6, occupied*unit/padding/(.84 if tail else 1)+6
        else:
            w, h = occupied*unit/padding+6, lines*unit/padding/(.84 if tail else 1)+6
        rw, rh = max(.10,w/frame["width"]), max(.10,h/frame["height"])
        if rw <= max_w and rh <= max_h:
            # 同じ面積なら極端な細長さを避ける。
            target_ratio=5 if flow == "horizontal" else .55
            cost = rw*rh + .001*abs(math.log(max(w/h,.001)/target_ratio))
            if flow == "horizontal":cost += .006*(lines-1)
            candidates.append((cost,rw,rh))
    if candidates:
        _, w, h = min(candidates)
        return w, h, False
    return max_w, max_h, True


def proposed_bubbles(project_id, page, frames, settings):
    bubbles, notes = [], []
    utterances_by_panel={p["panel"]:split_dialogue(p["dialogue"]) for p in page["panels"]}
    total=sum(len(v) for v in utterances_by_panel.values())+sum(bool(p["narration"].strip()) for p in page["panels"])
    for panel in sorted(page["panels"],key=lambda p:len(utterances_by_panel[p["panel"]]),reverse=True):
        utterances=utterances_by_panel[panel["panel"]]
        if total>60 and len(utterances)>1:
            total-=len(utterances)-1
            utterances_by_panel[panel["panel"]]=[Utterance("",panel["dialogue"],panel["dialogue"],True)]
            notes.append(f"{panel['panel']}コマ：1ページ60枠を超えるため、このコマの原文を保持しています。手動分割してください。")
    for panel, frame in zip(page["panels"], frames):
        utterances = utterances_by_panel[panel["panel"]]
        texts = [("dialogue",u.speaker,u.text,u.raw,u.needs_review) for u in utterances]
        if panel["narration"].strip():
            texts.append(("narration","",panel["narration"],panel["narration"],False))
        for index,(source,speaker,text,raw,review) in enumerate(texts):
            narration = source == "narration"
            flow, shape, tail = ("horizontal","rectangle",False) if narration else ("vertical","ellipse",True)
            scale = .022 if narration else .025
            w,h,overflow = size_for_text(text,flow,shape,tail,scale,frame,settings["width"],len(texts))
            # 同一コマは右→左。3個以上なら下段へ。自動で顔を検出した配置ではない。
            x = max(.01,1-w-.025) if index%2 == 0 else .025
            y = .025 if index < 2 else min(1-h-.025,.53)
            bubble = dict(id=str(uuid5(UUID(project_id),f"speech-v2:{page['page']}:{panel['panel']}:{source}:{index}:{raw}")),
                panel=panel["panel"],source=source,text=text,speaker=speaker,source_text=raw,needs_review=review,
                kind="narration" if narration else "speech",visible=True,box=dict(x=x,y=y,width=w,height=h),
                shape=shape,flow=flow,tail=tail,font_scale=scale,min_font_scale=.02,
                background="#ffffff",opacity=.88 if narration else 1.0,outline=not narration,text_layout="japanese_v2")
            bubbles.append(bubble)
            if review:notes.append(f"{panel['panel']}コマ：話者・発言境界を確定できません。原文を保持しています。手動で話者と本文を確認してください。")
            if overflow:notes.append(f"{panel['panel']}コマ：文字量が多いため、枠のサイズと文字の収まりを確認してください。")
        if len(texts)>2:notes.append(f"{panel['panel']}コマ：3枠以上あります。重なりと読み順を手動で確認してください。")
    return bubbles, notes
