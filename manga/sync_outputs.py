"""完成PNG/PDFを明示的に保持する。元画像・過去の完成ファイルを削除しない。"""
import fcntl
import json
import os
from pathlib import Path
from uuid import uuid4
from manga.models import now_iso,validate_id
from manga.storage import StorageError,write_json_atomic
from manga.sync_manifest import sha256,MAX_FILE_BYTES,MAX_TOTAL_BYTES,safe_path
from manga.review_context import load_context
from manga.studio_assets import production_fingerprint
from manga.page_render import decode_image


def output_index(directory,project_id):
    path=Path(directory)/validate_id(project_id)/'sync_outputs.json'
    try:
        data=json.loads(path.read_text())
        if data['schema_version']!=1 or data['project_id']!=project_id or not isinstance(data['items'],list):raise ValueError
        for item in data['items']:
            validate_id(item['id']);safe_path(item['path'])
            if item['path']!=f"sync_outputs/{item['id']}{Path(item['filename']).suffix}":raise ValueError
        return data
    except FileNotFoundError:return dict(schema_version=1,project_id=project_id,items=[])
    except (ValueError,KeyError,OSError,TypeError) as error:raise StorageError('完成ファイルの保存記録が不正です。上書きせず確認してください。') from error


def current_output_fingerprint(directory,project_id):
    context=load_context(directory,project_id,cached=False)
    if context.errors or not context.latest or not context.artwork or not context.composition:raise ValueError('完成ファイルの生成元を確認できません。')
    return production_fingerprint(context.latest,context.artwork,context.composition,context.root)


def save_output(directory,project_id,filename,content,expected_fingerprint):
    if Path(filename).name!=filename or Path(filename).suffix not in ('.png','.pdf'):raise ValueError('保存できる完成形式はPNG・PDFです。')
    if len(content)>MAX_FILE_BYTES:raise ValueError('完成ファイルの同期保存は1件10MiBまでです。出力サイズを下げてください。')
    if filename.endswith('.png'):
        with decode_image(content):pass
    elif not content.startswith(b'%PDF-') or b'%%EOF' not in content[-1024:]:raise ValueError('PDFの形式が不正です。')
    root=Path(directory)/validate_id(project_id);root.mkdir(parents=True,exist_ok=True)
    with (root/'.sync_outputs.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            if current_output_fingerprint(directory,project_id)!=expected_fingerprint:raise ValueError('生成後に制作データが変わりました。最新状態で出力し直してください。')
            data=output_index(directory,project_id)
            if any(i['sha256']==sha256(content) and i['source_fingerprint']==expected_fingerprint for i in data['items']):raise ValueError('この完成ファイルは保存済みです。')
            if sum(i['size'] for i in data['items'])+len(content)>MAX_TOTAL_BYTES:raise ValueError('完成ファイル保存は作品ごとに100MiBまでです。自動削除せず保存を停止しました。')
            identifier=str(uuid4());path=root/'sync_outputs'/f'{identifier}{Path(filename).suffix}'
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.resolve().is_relative_to(root.resolve()):raise StorageError('完成ファイルの保存先が領域外です。')
            with path.open('xb') as file:file.write(content);file.flush();os.fsync(file.fileno())
            item=dict(id=identifier,filename=filename,path=path.relative_to(root).as_posix(),size=len(content),sha256=sha256(content),saved_at=now_iso(),source_fingerprint=expected_fingerprint)
            data['items'].append(item);write_json_atomic(root/'sync_outputs.json',data);return item
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def read_output(directory,project_id,item):
    root=Path(directory)/validate_id(project_id);safe_path(item['path']);path=root/item['path']
    if not path.resolve().is_relative_to(root.resolve()):raise StorageError('完成ファイルの保存先が領域外です。')
    content=path.read_bytes()
    if len(content)!=item['size'] or sha256(content)!=item['sha256']:raise StorageError('完成ファイルのハッシュが一致しません。保存先を確認してください。')
    return content
