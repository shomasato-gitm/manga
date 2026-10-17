"""旧枠の外観を保ち、新しい文字枠は不透明度・可読サイズ・禁則を適用する。"""
from PIL import Image, ImageDraw, ImageColor
from manga.bubble_layout import body_geometry
from manga.japanese_text import TextLayoutError, draw_fitted_text


def draw_bubble(image,bubble,content,page_width,border,font):
    width,height=image.size
    box=bubble["box"]
    # 数値編集・コンポーネントからの不正な枠を黙って切り落とさない。
    from manga.page_layout import validate_rect
    validate_rect(box,"吹き出し",.10)
    x,y=border+round(box["x"]*(width-border*2)),border+round(box["y"]*(height-border*2))
    bw=max(1,min(round(box["width"]*(width-border*2)),width-border-x))
    bh=max(1,min(round(box["height"]*(height-border*2)),height-border-y))
    layer=Image.new("RGBA",(bw,bh),(0,0,0,0));draw=ImageDraw.Draw(layer)
    stroke=max(1,round(page_width*.0015))
    body_h,inset_x,inset_y=body_geometry(bubble,bw,bh)
    color=ImageColor.getrgb(bubble.get("background","#ffffff"))+(round(255*bubble.get("opacity",1.0)),)
    outline="black" if bubble.get("outline",True) else None
    if bubble["tail"]:
        draw.polygon([(round(bw*.45),max(0,body_h-stroke*2)),(round(bw*.30),bh-1),(round(bw*.70),max(0,body_h-stroke*2))],fill=color,outline=outline,width=stroke)
    bounds=(0,0,max(0,bw-1),max(0,body_h-1))
    if bubble["shape"] == "ellipse":draw.ellipse(bounds,fill=color,outline=outline,width=stroke)
    else:draw.rectangle(bounds,fill=color,outline=outline,width=stroke)
    try:
        tw,th=bw-inset_x*2,body_h-inset_y*2
        if tw < 1 or th < 1:raise TextLayoutError("吹き出しが小さすぎます。サイズを広げてください。")
        minimum = round(page_width*bubble["min_font_scale"]) if "min_font_scale" in bubble else max(5,round(page_width*.008))
        text_layer,chosen=draw_fitted_text((tw,th),content,bubble["flow"],font,max(1,round(page_width*bubble["font_scale"])),
                         max(1,min(minimum,round(page_width*bubble["font_scale"]))),kinsoku=bubble.get("text_layout")=="japanese_v2")
        layer.alpha_composite(text_layer,(inset_x,inset_y))
    except TextLayoutError:
        draw.rectangle((0,0,bw-1,bh-1),outline="red",width=stroke)
        image.paste(layer,(x,y),layer)
        raise
    image.paste(layer,(x,y),layer)
    return chosen
