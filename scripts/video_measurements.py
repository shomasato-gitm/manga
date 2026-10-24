"""短い検証用トーンMP4の音声・焼込字幕をデコードして開始誤差を測る。"""
import array
import io
import math
import subprocess
import sys
from PIL import Image

from manga.japanese_text import find_japanese_font
from manga.video_models import FORMATS,compile_plan
from manga.video_visuals import subtitle_image
from manga.video_process import executables


def decode(path,args):
    result=subprocess.run([executables()['ffmpeg'],'-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(path),*args,'pipe:1'],
        capture_output=True,timeout=30,check=True)
    if len(result.stdout)>32*1024*1024:raise ValueError('同期計測は短い検証用動画だけを対象にします。')
    return result.stdout


def measure(path,plan,timeline):
    schedule=compile_plan(plan,timeline);units=[u for r in schedule['rows'] for u in r['utterances']]
    if schedule['duration_ms']>10000:raise ValueError('自動同期計測は10秒以内の検証用トーン動画に限ります。')
    samples=array.array('h');samples.frombytes(decode(path,['-map','0:a:0','-ac','1','-ar','16000','-f','s16le']))
    if sys.byteorder!='little':samples.byteswap()
    frequencies={a['id']:220+int(a['settings']['voice_id'].split('-')[1])*60 for a in timeline['assets']}
    tones=sorted(set(frequencies.values()));coeffs={f:2*math.cos(2*math.pi*f/16000) for f in tones}
    classes=[];window=160;step=80
    for start in range(0,len(samples)-window+1,step):
        part=samples[start:start+window]
        if sum(x*x for x in part)/window<10000:label=None
        else:
            powers={}
            for f in tones:
                a=b=0.0
                for value in part:a,b=value+coeffs[f]*a-b,a
                powers[f]=a*a+b*b-coeffs[f]*a*b
            label=max(powers,key=powers.get)
        classes.append(((start+window/2)/16,label))
    transitions=[];previous=None
    for ms,label in classes:
        if label!=previous:
            if label is not None:transitions.append((ms,label))
            previous=label
    size=FORMATS[plan['kind']];width=384;y=round(size[1]*(.0 if plan['subtitle_position']=='top' else .70))
    height=round(size[1]*.30);height=min(height,size[1]-y);small_h=round(height*width/size[0])
    raw=decode(path,['-map','0:v:0','-vf',f'crop={size[0]}:{height}:0:{y},scale={width}:{small_h},format=gray','-f','rawvideo'])
    frame_size=width*small_h;frames=[]
    for offset in range(0,len(raw),frame_size):frames.append({i for i,p in enumerate(raw[offset:offset+frame_size]) if p>180})
    font=find_japanese_font(plan['font_path']);measurements=[]
    for unit in units:
        expected=unit['start_ms'];tone=frequencies[unit['asset_id']]
        candidates=[ms for ms,f in transitions if f==tone and abs(ms-expected)<=250]
        if not candidates:raise ValueError('音声トーンの開始を許容範囲で検出できません。')
        actual=min(candidates,key=lambda x:abs(x-expected))
        layer=subtitle_image(unit['text'],size,font,plan['subtitle_position'])
        background=Image.new('RGBA',size,'black');background.alpha_composite(layer)
        image=background.convert('L').crop((0,y,size[0],y+height)).resize((width,small_h),Image.Resampling.BICUBIC)
        mask={i for i,p in enumerate(image.tobytes()) if p>180}
        matches=[]
        for i,pixels in enumerate(frames):
            t=i*1000/plan['fps']
            if abs(t-expected)>250:continue
            score=len(mask & pixels)/max(1,len(mask | pixels))
            if score>.50:matches.append((t,score))
        if not matches:raise ValueError('焼込字幕の開始を許容範囲で確認できません。')
        caption,score=min(matches,key=lambda x:abs(x[0]-expected))
        measurements.append(dict(utterance_id=unit['id'],expected_ms=expected,audio_onset_ms=actual,
            audio_error_ms=round(actual-expected,3),subtitle_frame_ms=round(caption,3),subtitle_error_ms=round(caption-expected,3),mask_iou=round(score,3)))
    return dict(tolerance_ms=250,measurements=measurements,max_audio_error_ms=max(abs(r['audio_error_ms']) for r in measurements),
                max_subtitle_error_ms=max(abs(r['subtitle_error_ms']) for r in measurements))
