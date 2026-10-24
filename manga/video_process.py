"""FFmpegの実行境界。ネットワーク入力・shell・無制限待機を使用しない。"""
import json
import math
import os
import signal
import subprocess
import time

from manga.video_prereqs import probe_executables


class VideoError(ValueError):pass
class VideoCancelled(VideoError):pass


def executables():
    tools=probe_executables()
    if not all(tools.values()):raise VideoError('ffmpegとffprobeが必要です。動画機能だけ停止します。Macの導入手順を確認してください。')
    return tools


def run(args,*,cwd,timeout=180,cancel=None):
    started=time.monotonic()
    # Diagnostic output stays in a bounded on-disk log, never an unbounded PIPE.
    log=cwd/'process.log'
    with log.open('wb') as file:
        process=subprocess.Popen(args,cwd=str(cwd),stdin=subprocess.DEVNULL,stdout=file,stderr=subprocess.STDOUT,
                                 shell=False,start_new_session=True)
        try:
            while process.poll() is None:
                if cancel and cancel():raise VideoCancelled('動画処理を中断しました。元素材・履歴は保持しています。')
                if time.monotonic()-started>timeout:raise VideoError('FFmpegの制限時間を超えました。軽量設定で再試行してください。')
                if log.stat().st_size>1024*1024:raise VideoError('FFmpegの診断ログ上限を超えました。')
                time.sleep(.05)
            if process.returncode:raise VideoError('FFmpeg処理が失敗しました。入力形式・容量・コーデックを確認してください。')
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=2)


def probe(path,*,forced_format=None):
    tool=executables()['ffprobe']
    args=[tool,'-v','error','-protocol_whitelist','file,pipe']
    if forced_format:args+=['-f',forced_format]
    args+=['-show_entries','stream=index,codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_rate,channels,duration:format=duration,format_name','-of','json',str(path.resolve())]
    try:
        result=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,timeout=15,check=True,shell=False)
        if len(result.stdout)>1024*1024:raise ValueError
        return json.loads(result.stdout)
    except (subprocess.SubprocessError,OSError,ValueError) as error:raise VideoError('ffprobeで素材／動画を検証できません。') from error


def verify_mp4(path,size,fps,duration_ms):
    try:return _verify_mp4(path,size,fps,duration_ms)
    except (KeyError,TypeError,ValueError,ZeroDivisionError,OverflowError) as error:
        if isinstance(error,VideoError):raise
        raise VideoError('動画の検証結果に不正な数値・必須項目があります。') from error


def _verify_mp4(path,size,fps,duration_ms):
    raw=probe(path);videos=[s for s in raw['streams'] if s['codec_type']=='video'];audio=[s for s in raw['streams'] if s['codec_type']=='audio']
    if len(videos)!=1 or len(audio)!=1:raise VideoError('映像・音声ストリームが揃っていません。')
    v,a=videos[0],audio[0]
    n,d=map(int,v['avg_frame_rate'].split('/'))
    actual=float(raw['format']['duration'])*1000
    audio_duration=float(a.get('duration',raw['format']['duration']))*1000
    if not d or not math.isfinite(actual) or actual<=0 or not math.isfinite(audio_duration) or audio_duration<=0:raise VideoError('動画・音声の実尺が不正です。')
    if (v['width'],v['height'])!=tuple(size) or v['codec_name']!='h264' or a['codec_name']!='aac' or v['pix_fmt']!='yuv420p' or abs(n/d-fps)>.001 or abs(actual-duration_ms)>250:
        raise VideoError('動画の寸法・codec・FPS・実尺が出力計画と一致しません。')
    if abs(audio_duration-duration_ms)>250:raise VideoError('音声トラックの実尺が一致しません。')
    return dict(width=v['width'],height=v['height'],video_codec=v['codec_name'],audio_codec=a['codec_name'],fps=n/d,
                duration_ms=round(actual),audio_sample_rate=int(a['sample_rate']),audio_channels=a['channels'],pixel_format=v['pix_fmt'])
