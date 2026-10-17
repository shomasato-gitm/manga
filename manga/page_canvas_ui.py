"""依存ライブラリなしのローカルCanvas。Streamlitの公式コンポーネント通信だけを使う。"""
import base64
from pathlib import Path
import streamlit.components.v1 as components
from manga.page_layout import dimensions, pixel_frames, bubble_text

_component=components.declare_component("manga_page_canvas",path=str(Path(__file__).with_name("page_canvas")))


def canvas_boxes(page,config,settings):
    frames={p["panel"]:f for p,f in zip(page["panels"],pixel_frames(page,config,settings))}
    border=round(settings["width"]*settings["border"]/100)
    return [dict(id=b["id"],box=b["box"],label=b.get("speaker") or ("ナレーション" if b["source"]=="narration" else "セリフ"),
        frame=dict(x=frames[b["panel"]]["x"]+border,y=frames[b["panel"]]["y"]+border,
                   width=max(1,frames[b["panel"]]["width"]-2*border),height=max(1,frames[b["panel"]]["height"]-2*border)))
        for b in config["bubbles"] if b["visible"] and bubble_text(b,page["panels"][b["panel"]-1]).strip()]


def page_canvas(png,page,config,settings,token,selected,key):
    width,height=dimensions(settings)
    return _component(image=base64.b64encode(png).decode(),width=width,height=height,
        boxes=canvas_boxes(page,config,settings),token=token,selected=selected,key=key,default=None)
