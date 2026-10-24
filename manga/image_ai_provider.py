"""無料fixture専用。課金の最大量が確認できない実APIは接続しない。"""
import hashlib
from io import BytesIO
from pathlib import PurePath

from PIL import Image, ImageDraw

from manga.image_ai_models import SIZES
from manga.images import prepare_image


def offline_only(provider):
    if provider not in ('mock','manual'):
        raise ValueError('有料画像APIは未対応です。最大請求量を公式資料で確定できないため、キー・承認・残額があっても予約・送信しません。')


def filename(value):
    if not isinstance(value,str) or not value or len(value)>200 or any(c in value for c in ('/','\\','\x00','\n','\r')) or value in ('.','..'):
        raise ValueError('画像ファイル名にはパス・改行等を含めないでください。')
    return value


def manual_image(content,name):
    name=filename(name)
    png,width,height=prepare_image(content)
    with Image.open(BytesIO(content)) as image:
        expected={'.png':'PNG','.jpg':'JPEG','.jpeg':'JPEG','.webp':'WEBP'}.get(PurePath(name).suffix.lower())
        if image.format!=expected: raise ValueError('画像の拡張子と実際の形式が一致しません。')
    return png,width,height


def mock_image(job):
    width,height=SIZES[job['plan']['options']['size']]
    color=tuple(60+b%130 for b in hashlib.sha256(job['id'].encode()).digest()[:3])
    image=Image.new('RGB',(width,height),'#edf1f5'); draw=ImageDraw.Draw(image)
    draw.rectangle((12,12,width-13,height-13),outline='black',width=5)
    draw.rectangle((30,height//2,width-30,height-70),fill=color)
    people=job['plan']['source']['characters']
    for index,person in enumerate(people):
        x=round(width*(index+1)/(len(people)+1)); y=height//3; radius=max(20,min(width//(len(people)*5+5),height//9))
        draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=color,outline='black',width=3)
        draw.line((x,y+radius,x,y+radius*3),fill='black',width=7)
        draw.text((max(20,x-radius),y+radius*3+8),person['id'][:8],fill='black')
    draw.rectangle((20,20,width-20,82),fill='white')
    draw.text((35,36),'TEST IMAGE - NOT AI - NOT FOR PUBLICATION',fill='red',stroke_width=1)
    draw.text((35,95),'offline fixture / '+job['id'][:8],fill='black')
    stream=BytesIO(); image.save(stream,'PNG')
    return stream.getvalue()
