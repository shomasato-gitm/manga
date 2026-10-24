"""Pillowで安全な注目領域・日本語字幕を準備し、文字列のfilter注入を避ける。"""
import io
from PIL import Image,ImageDraw,ImageOps,ImageFilter

from manga.japanese_text import fit_text


def crop_box(size,aspect,focus):
    """手動指定の重要領域を完全に含む最大crop。無理なら全体表示へ戻す。"""
    width,height=size;x,y,w,h=focus
    cw=min(width,height*aspect);ch=cw/aspect
    if cw+1e-6<w*width or ch+1e-6<h*height:return None
    left=max(0,min(width-cw,(x+w/2)*width-cw/2));top=max(0,min(height-ch,(y+h/2)*height-ch/2))
    if left>x*width+1e-6 or top>y*height+1e-6 or left+cw<(x+w)*width-1e-6 or top+ch<(y+h)*height-1e-6:return None
    return (left,top,left+cw,top+ch)


def canvas(content,size,edit,subtitle_position,*,reserve_hook=False):
    width,height=size
    with Image.open(io.BytesIO(content)) as raw:
        if raw.format!='PNG' or raw.width*raw.height>12_000_000:raise ValueError('動画用画像はPNG・1200万画素までです。')
        raw.load();source=raw.convert('RGB')
    background=ImageOps.fit(source,(max(1,width//8),max(1,height//8))).filter(ImageFilter.GaussianBlur(12)).resize(size)
    background=Image.blend(background,Image.new('RGB',size,'#101018'),.65)
    area=(round(width*.84),round(height*(.42 if reserve_hook else .64)));box=None;fallback=False
    if edit['framing']=='focus':
        box=crop_box(source.size,area[0]/area[1],edit['focus'])
        fallback=box is None
    visible=source.crop(box) if box else source
    fitted=ImageOps.contain(visible,area,Image.Resampling.LANCZOS)
    top=round(height*(.29 if reserve_hook or subtitle_position=='top' else .06))
    position=((width-fitted.width)//2,top+(area[1]-fitted.height)//2)
    background.paste(fitted,position)
    return background,dict(crop=list(box) if box else None,focus_fallback=fallback,
        foreground=[position[0],position[1],fitted.width,fitted.height],note='顔検出ではなく手動の重要領域。fitは全体を保持。モーション用余白を確保。')


def subtitle_image(text,size,font,position):
    width,height=size;layer=Image.new('RGBA',size,(0,0,0,0))
    if not text:return layer
    if not isinstance(text,str) or len(text)>3000:raise ValueError('字幕が長すぎます。音声工程で発話を分割してください。')
    inner=(round(width*.82),round(height*.19))
    _,pil,(lines,step)=fit_text(text,*inner,'horizontal',font,round(width*.038),max(24,round(width*.024)),kinsoku=True)
    left=round(width*.09);top=round(height*(.07 if position=='top' else .75));padding=round(width*.015)
    draw=ImageDraw.Draw(layer)
    draw.rounded_rectangle((left-padding,top-padding,left+inner[0]+padding,top+len(lines)*step+padding),radius=padding,fill=(8,8,16,215))
    for i,line in enumerate(lines):
        bounds=pil.getbbox(line)
        draw.text((left-bounds[0],top+i*step-bounds[1]),line,font=pil,fill='white',stroke_width=max(1,round(width/600)),stroke_fill='black')
    return layer


def png(image):
    output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()


def motion_filter(motion,frames,size,fps):
    count=max(1,frames-1);ratio=f'on/{count}'
    zoom='1' if motion=='still' else f'1.04-0.04*{ratio}' if motion=='zoom_out' else f'1+0.04*{ratio}' if motion=='zoom_in' else '1.04'
    x=f'(iw-iw/zoom)*(1-{ratio})' if motion=='pan_left' else f'(iw-iw/zoom)*{ratio}' if motion=='pan_right' else '(iw-iw/zoom)/2'
    # All expressions are selected from constants or validated integers.
    return f"zoompan=z='{zoom}':x='{x}':y='(ih-ih/zoom)/2':d=1:s={size[0]}x{size[1]}:fps={fps}"
