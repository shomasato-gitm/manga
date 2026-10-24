"""既存WAVを整数ms位置へ配置する。音声生成APIは呼ばない。"""
import io
import wave

from manga.voice_audio import inspect_wav


def arrange_voice(path,schedule,assets):
    """16kHzモノラルのサンプル位置を固定。重ならない発話を逐次書き込む。"""
    units=sorted((u for r in schedule['rows'] for u in r['utterances']),key=lambda u:u['start_ms'])
    total=(schedule['frames']*16000+schedule['fps']-1)//schedule['fps'];position=0
    def silence(wav,frames):
        while frames:
            n=min(frames,16000);wav.writeframesraw(b'\0\0'*n);frames-=n
    with wave.open(str(path),'wb') as target:
        target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000)
        for unit in units:
            start=unit['start_ms']*16
            if start<position:raise ValueError('発話が重複しています。編集計画を確認してください。')
            silence(target,start-position)
            content=assets(unit['asset_id']);info=inspect_wav(content)
            with wave.open(io.BytesIO(content),'rb') as source:
                remaining=info['frames']
                while remaining:
                    n=min(remaining,16000);target.writeframesraw(source.readframes(n));remaining-=n
            position=start+info['frames']
        if position>total:raise ValueError('音声が動画の終了を超えます。発話は切り詰めません。')
        silence(target,total-position)
