"""JSONと実ファイルを照合する。リンク・欠損・古い版は自動承認しない。"""
import hashlib
import io
import json
from pathlib import Path,PurePosixPath

from PIL import Image

from manga.storage import StorageError
from manga.voice_audio import inspect_wav,verify_with_ffprobe
from manga.voice_contract import validate_timeline
from manga.voice_models import digest
from manga.voice_timeline import build_timeline


def file_bytes(root,relative,limit):
    raw=PurePosixPath(relative)
    if raw.is_absolute() or '..' in raw.parts or '\\' in relative or not raw.parts:raise ValueError('素材の相対パスが不正です。')
    path=root.joinpath(*raw.parts)
    for part in [root,path,*path.parents]:
        if part==root.parent:break
        if part.is_symlink():raise StorageError('素材へのリンクを拒否しました。')
    if not path.is_file() or not 0<path.stat().st_size<=limit:raise ValueError('素材ファイルが欠損・空・容量超過です。')
    content=path.read_bytes()
    if not 0<len(content)<=limit:raise ValueError('素材の読込中に容量が変更されました。')
    return content


def snapshot(voice,timeline_id):
    data=voice.store.load();record=next((r for r in data['timelines'] if r['id']==timeline_id),None)
    if record is None:raise ValueError('先に音声制作でタイムラインを保存してください。')
    validate_id_path=f'voice/exports/{timeline_id}.json'
    if record['paths']['json']!=validate_id_path:raise ValueError('タイムラインの保存先が不正です。')
    content=file_bytes(voice.store.root,validate_id_path,16*1024*1024)
    try:timeline=validate_timeline(json.loads(content))
    except (ValueError,RecursionError) as error:raise ValueError('音声タイムラインの形式が不正です。') from error
    if timeline['project_id']!=voice.project_id or timeline['id']!=timeline_id:raise ValueError('タイムラインの作品・版が一致しません。')
    images={};audio={};errors=[];warnings=list(timeline['warnings'])
    for scene in timeline['scenes']:
        label=f"{scene['page']}ページ{scene['panel']}コマ"
        image=scene['image']
        try:
            if image is None:raise ValueError('採用画像がありません。')
            payload=file_bytes(voice.store.root,image['path'],25*1024*1024)
            if hashlib.sha256(payload).hexdigest()!=image['sha256']:raise ValueError('画像ハッシュが一致しません。')
            with Image.open(io.BytesIO(payload)) as raw:
                if raw.format!='PNG' or raw.width*raw.height>12_000_000:raise ValueError('PNG・1200万画素までです。')
                raw.verify()
            images[scene['id']]=dict(path=image['path'],sha256=image['sha256'])
        except (OSError,ValueError,StorageError) as error:errors.append(label+'：'+str(error))
    for asset in timeline['assets']:
        try:
            payload=file_bytes(voice.store.root,asset['path'],25*1024*1024);info=inspect_wav(payload)
            if any(info[k]!=asset['info'][k] for k in ('sha256','frames','duration_ms','sample_rate','channels')):raise ValueError('WAVハッシュ・実尺が一致しません。')
            verify_with_ffprobe(voice.store.safe_path(asset['path']),info)
            audio[asset['id']]=dict(path=asset['path'],sha256=asset['info']['sha256'])
            if not asset.get('rights') or asset['rights'].get('status') not in ('test-only','user-attested'):warnings.append('音声の利用条件が未確認です。')
        except (OSError,ValueError,StorageError) as error:errors.append('音声 '+asset['id']+'：'+str(error))
    if errors:raise ValueError('動画素材を準備してください。\n'+'\n'.join(errors[:30]))
    current=build_timeline(voice,**record['options'])
    # IDs/created_at differ on a fresh build; every material field must match.
    stable=lambda t:{k:v for k,v in t.items() if k not in ('id','timeline_version_id','created_at')}
    if digest(stable(timeline))!=digest(stable(current)):raise ValueError('脚本・画像・音声・確認状態が変わった古いタイムラインです。音声工程で新しい版を保存してください。')
    return dict(timeline=timeline,images=images,audio=audio,timeline_sha256=hashlib.sha256(content).hexdigest(),warnings=list(dict.fromkeys(warnings)))
