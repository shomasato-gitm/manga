"""検証済み素材をローカルFFmpegだけでH.264/AACへ合成する。"""
import hashlib
import json
import math
import os
import time
from pathlib import Path

from manga.voice_models import digest
from manga.video_models import FORMATS,compile_plan
from manga.video_visuals import canvas,png,subtitle_image,motion_filter
from manga.video_audio import arrange_voice
from manga.video_process import run,executables,verify_mp4,VideoError


def encoder(plan):
    return ['-c:v','libx264','-preset','ultrafast' if plan['quality']=='fast' else 'veryfast','-crf','22',
            '-pix_fmt','yuv420p','-threads','2']


def render_scene(store,job_dir,index,row,plan,font,read_image,*,cancel,commands,force=False):
    size=FORMATS[plan['kind']];fps=plan['fps'];actual_start=row['start_frame']*1000/fps
    body=read_image(row['source_scene']['id'])
    key=digest(dict(renderer='manga-ffmpeg-v3',image=hashlib.sha256(body).hexdigest(),size=size,
                    row=row,plan={k:plan[k] for k in ('fps','quality','subtitle_position','hook')},font=font.signature))
    relative=f'video/cache/{key}.mp4';cached=store.safe(relative);receipt=store.safe(f'video/cache/{key}.json')
    if not force and cached.is_file() and receipt.is_file():
        raw=json.loads(receipt.read_text())
        if cached.stat().st_size>512*1024*1024 or sha_file(cached)!=raw['sha256']:raise VideoError('動画キャッシュが変更されています。元ファイルを削除せず確認してください。')
        return cached,raw['visual'],True
    directory=job_dir/f'clip-{index:03d}';directory.mkdir(exist_ok=False)
    image,visual=canvas(body,size,row['edit'],plan['subtitle_position'],reserve_hook=index==0 and bool(plan['hook']));image.save(directory/'base.png')
    args=[executables()['ffmpeg'],'-v','error','-nostdin','-y','-filter_complex_threads','1',
          '-protocol_whitelist','file,pipe','-loop','1','-framerate',str(fps),'-i','base.png']
    effect=motion_filter(row['edit']['motion'],row['frames'],size,fps)
    duration=row['frames']/fps
    visual['transition_applied']='fade' if row['edit']['transition']=='fade' and duration>=.75 else 'cut'
    if visual['transition_applied']=='fade':
        fade=min(.18,duration/4)
        effect+=f',fade=t=in:st=0:d={fade:.6f},fade=t=out:st={duration-fade:.6f}:d={fade:.6f}'
    # Fade pictures only. Captions remain readable for their entire WAV interval.
    filters=[f'[0:v]{effect}[v0]']
    captions=[]
    for u in row['utterances']:
        captions.append((u['text'],max(0,(u['start_ms']-actual_start)/1000),(u['end_ms']-actual_start)/1000,plan['subtitle_position']))
    if index==0 and plan['hook']:captions.append((plan['hook'],0,min(3,row['frames']/fps),'top' if plan['subtitle_position']=='bottom' else 'bottom'))
    for i,(text,start,end,position) in enumerate(captions,1):
        subtitle_image(text,size,font,position).save(directory/f'caption-{i}.png')
        args+=['-protocol_whitelist','file,pipe','-loop','1','-framerate',str(fps),'-i',f'caption-{i}.png']
        filters.append(f"[v{i-1}][{i}:v]overlay=0:0:enable='gte(t,{start:.6f})*lt(t,{end:.6f})'[v{i}]")
    label=f'v{len(captions)}'
    args+=['-filter_complex',';'.join(filters),'-map',f'[{label}]','-frames:v',str(row['frames']),'-an',*encoder(plan),'-fs','536870912','clip.mp4']
    commands.append(dict(cwd=str(directory.relative_to(store.root)),argv=args))
    run(args,cwd=directory,timeout=180,cancel=cancel)
    target=directory/'clip.mp4'
    # Cache is immutable. A fresh failed output never becomes a completed export.
    cache=store.immutable(relative,target.read_bytes())
    store.immutable(f'video/cache/{key}.json',json.dumps(dict(sha256=sha_file(cache),visual=visual)).encode())
    return cache,visual,False


def sha_file(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda:file.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


def render_format(store,job_dir,plan,timeline,font,materials,*,cancel,progress,commands,force=False):
    schedule=compile_plan(plan,timeline);schedule['fps']=plan['fps']
    directory=job_dir/plan['kind'];directory.mkdir(exist_ok=False)
    clips=[];visuals=[];hits=0
    for index,row in enumerate(schedule['rows']):
        clip,visual,hit=render_scene(store,directory,index,row,plan,font,materials['image'],cancel=cancel,commands=commands,force=force)
        # Concat names are generated internally; user text/path is never inserted.
        name=f'part-{index:03d}.mp4';os.link(clip,directory/name)
        clips.append(f"file '{name}'");visuals.append(visual);hits+=int(hit)
        progress(index+1,len(schedule['rows']))
    (directory/'concat.txt').write_text('\n'.join(clips)+'\n',encoding='ascii')
    arrange_voice(directory/'voice.wav',schedule,materials['audio'])
    args=[executables()['ffmpeg'],'-v','error','-nostdin','-y','-filter_complex_threads','1',
          '-protocol_whitelist','file,pipe','-f','concat','-safe','1','-i','concat.txt',
          '-protocol_whitelist','file,pipe','-i','voice.wav']
    extras=([plan['bgm']] if plan['bgm'] else [])+plan['sfx'];filters=['[1:a]aresample=48000[a0]'];labels=['[a0]']
    for i,extra in enumerate(extras,2):
        material=materials['extra'](extra['id']);name=f'extra-{i}.{material["extension"]}'
        os.link(material['path'],directory/name)
        args+=['-protocol_whitelist','file,pipe','-f',material['extension'],'-i',name]
        duration=schedule['frames']/plan['fps'];offset=extra['start_ms']
        usable=min(material['duration_ms']/1000,max(0,duration-offset/1000));fade=min(extra['fade_ms']/1000,usable/2)
        filters.append(f'[{i}:a]atrim=duration={usable:.6f},asetpts=PTS-STARTPTS,volume={extra["volume"]:.6f},afade=t=in:d={fade:.6f},afade=t=out:st={max(0,usable-fade):.6f}:d={fade:.6f},adelay={offset}|{offset}[a{i}]')
        labels.append(f'[a{i}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[audio]')
    output=directory/(plan['kind']+'.mp4')
    args+=['-filter_complex',';'.join(filters),'-map','0:v:0','-map','[audio]','-c:v','copy','-c:a','aac','-b:a','160k',
           '-ar','48000','-ac','2','-t',f'{schedule["frames"]/plan["fps"]:.6f}','-movflags','+faststart','-fs','536870912',output.name]
    commands.append(dict(cwd=str(directory.relative_to(store.root)),argv=args));run(args,cwd=directory,timeout=300,cancel=cancel)
    metadata=verify_mp4(output,FORMATS[plan['kind']],plan['fps'],schedule['frames']*1000/plan['fps'])
    return dict(kind=plan['kind'],staged=str(output.relative_to(store.root)),sha256=sha_file(output),metadata=metadata,
        planned_duration_ms=schedule['duration_ms'],cache_hits=hits,scenes=len(schedule['rows']),visuals=visuals,subtitles=schedule['subtitles'])
