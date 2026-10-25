"""PCM/WAVの検証・実測。モックは人の声ではない確認用トーン。"""
import hashlib
import io
import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

MAX_WAV_BYTES = 25*1024*1024
MAX_SECONDS = 600


def inspect_source_wav(content):
    """受入は自己完結したPCM WAVのみ。FFmpegへ任意URL/プレイリストを渡さない。"""
    if not isinstance(content,bytes) or not 44<=len(content)<=MAX_WAV_BYTES or content[:4]!=b'RIFF' or content[8:12]!=b'WAVE':
        raise ValueError('PCM WAVファイル（25MiB以下）を選択してください。')
    try:
        with wave.open(io.BytesIO(content),'rb') as wav:
            n,rate,ch,width=wav.getnframes(),wav.getframerate(),wav.getnchannels(),wav.getsampwidth()
            if wav.getcomptype()!='NONE' or ch not in (1,2) or width not in (1,2,3,4) or not 8000<=rate<=96000 or not 0<n<=rate*MAX_SECONDS:
                raise ValueError('PCM WAVは1〜2チャンネル、8〜96kHz、10分以下にしてください。')
            if len(wav.readframes(n))!=n*ch*width:raise ValueError('元WAVが欠損しています。')
    except (wave.Error,EOFError):raise ValueError('未対応または壊れたWAVです。PCM WAVとして書き出してください。') from None
    return dict(sha256=hashlib.sha256(content).hexdigest(),frames=n,sample_rate=rate,channels=ch,size=len(content))


def normalize_wav(content):
    inspect_source_wav(content)
    executable=shutil.which('ffmpeg')
    if not executable:raise ValueError('WAV変換にFFmpegが必要です。MacへFFmpegを導入して再開してください。')
    with TemporaryDirectory(prefix='manga-voice-') as folder:
        source=Path(folder)/'source.wav';target=Path(folder)/'normalized.wav'
        source.write_bytes(content)
        try:
            subprocess.run([executable,'-nostdin','-v','error','-n','-protocol_whitelist','file','-f','wav','-i',str(source),
                            '-map','0:a:0','-map_metadata','-1','-vn','-ac','1','-ar','16000','-c:a','pcm_s16le',
                            '-threads','1','-f','wav',str(target)],capture_output=True,timeout=60,check=True)
            if target.stat().st_size>MAX_WAV_BYTES:raise ValueError('変換後WAVが容量上限を超えました。')
            result=target.read_bytes()
        except (OSError,subprocess.SubprocessError):raise ValueError('WAVを変換できません。元ファイルを保持して停止しました。形式とFFmpegを確認してください。') from None
        inspect_wav(result)
        return result


def pcm_wav(pcm,sample_rate=16000):
    if not isinstance(pcm,bytes) or not pcm or len(pcm)%2 or len(pcm)>MAX_WAV_BYTES-44:
        raise ValueError('音声PCMが空・不正・容量超過です。')
    target = io.BytesIO()
    with wave.open(target,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(sample_rate);wav.writeframes(pcm)
    result = target.getvalue()
    inspect_wav(result)
    return result


def inspect_wav(content):
    if not isinstance(content,bytes) or not 44<=len(content)<=MAX_WAV_BYTES:
        raise ValueError('WAVの容量が不正です。')
    try:
        with wave.open(io.BytesIO(content),'rb') as wav:
            frames,rate,channels,width = wav.getnframes(),wav.getframerate(),wav.getnchannels(),wav.getsampwidth()
            if channels!=1 or width!=2 or rate!=16000 or not 0<frames<=rate*MAX_SECONDS or wav.getcomptype()!='NONE':
                raise ValueError('16kHz・16bit・モノラルPCM WAV（最大10分）が必要です。')
            if len(wav.readframes(frames))!=frames*channels*width:
                raise ValueError('WAVが途中で欠損しています。')
    except (wave.Error,EOFError) as error:
        raise ValueError('WAVを読み込めません。') from error
    return dict(frames=frames,sample_rate=rate,channels=channels,duration_ms=(frames*1000+rate-1)//rate,
                sha256=hashlib.sha256(content).hexdigest(),size=len(content),measurement='wave')


def verify_with_ffprobe(path,info):
    executable = shutil.which('ffprobe')
    if not executable:return dict(info)
    try:
        response = subprocess.run([executable,'-v','error','-show_entries','format=duration:stream=codec_name,sample_rate,channels',
                                   '-of','json',str(path.resolve())],capture_output=True,timeout=10,check=True)
        value = json.loads(response.stdout)
        streams = value['streams']
        if len(streams)!=1 or streams[0]['codec_name']!='pcm_s16le' or int(streams[0]['sample_rate'])!=16000 or streams[0]['channels']!=1:
            raise ValueError
        duration = float(value['format']['duration'])*1000
        if not math.isfinite(duration) or abs(duration-info['duration_ms'])>2:raise ValueError
    except (subprocess.SubprocessError,OSError,ValueError,KeyError,TypeError) as error:
        raise ValueError('ffprobeの音声実測結果がWAVと一致しません。') from error
    return dict(info,measurement='wave+ffprobe')


class MockVoice:
    def synthesize(self,text,settings):
        # Duration here is only fixture construction; subtitles use the WAV frame count.
        seconds = min(3.0,.25+len(text)*.02)/settings['speed']
        frequency = 220+int(settings['voice_id'].split('-')[1])*60
        level = {'normal':900,'angry':1500,'sad':600,'whisper':250}[settings['emotion']]
        pcm = b''.join(struct.pack('<h',int(level*math.sin(2*math.pi*frequency*i/16000))) for i in range(int(seconds*16000)))
        return pcm_wav(pcm)
