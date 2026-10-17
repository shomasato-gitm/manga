"""既存ストアを隔離フォルダで読み、既存の検証規則をそのまま利用する。"""
import tempfile
from io import BytesIO
from pathlib import Path
from manga.sync_manifest import decode_json
from manga.story import normalize_story
from manga.story_storage import StoryStore
from manga.artwork_models import validate_artwork
from manga.page_storage import PageStore
from manga.editorial_storage import EditorialStore
from manga.assistant_repository import validate_assistant
from manga.review_models import validate_review
from manga.studio_storage import StudioStore
from manga.models import Project
from manga.page_render import decode_image

STORES=('story_history.json','artwork.json','composition.json','editorial.json','assistant.json','review.json','studio.json')


def _validate_payload(files,project_id,manifest):
    decoded={}
    for path,content in files.items():
        allowed=path in (*STORES,'story.json','project.json','pdca.json') or path.startswith(('images/','exports/','attachments/','responses/'))
        if not allowed:raise ValueError(f'同期対象外のファイルです：{path}')
        if path.endswith('.json'):decoded[path]=decode_json(content)
        elif path.endswith('.png'):
            with decode_image(content):pass
        elif path.startswith('exports/') and path.endswith('.pdf'):
            if not content.startswith(b'%PDF-') or b'%%EOF' not in content[-1024:]:raise ValueError('完成PDFの形式が不正です。')
        else:raise ValueError('同期するバイナリはPNG・PDFに限定しています。')
    if 'story.json' not in decoded:raise ValueError('互換形式のstory.jsonが必要です。')
    normalized=normalize_story(decoded['story.json']).story
    if decoded['story.json'].get('schema_version')!=1 or normalized!=decoded['story.json']:raise ValueError('story.jsonは既存のschema_version: 1の正規形式にしてください。')
    with tempfile.TemporaryDirectory(prefix='manga-sync-validation-') as temp:
        directory=Path(temp);root=directory/project_id;root.mkdir()
        for name in STORES:
            if name in files:(root/name).write_bytes(files[name])
        if 'project.json' in decoded:
            if Project.from_dict(decoded['project.json']).id!=project_id:raise ValueError('作品IDが一致しません。')
        history=StoryStore(directory,project_id).load_history()
        if history and (history[-1]['id']!=manifest['story_version_id'] or history[-1]['story']!=normalized):raise ValueError('ストーリー履歴と同期する脚本・版が一致しません。')
        art=validate_artwork(decoded['artwork.json'],project_id) if 'artwork.json' in decoded else None
        if 'composition.json' in decoded:PageStore(directory,project_id).load()
        if 'editorial.json' in decoded:EditorialStore(directory,project_id).load()
        if 'assistant.json' in decoded:validate_assistant(decoded['assistant.json'],project_id)
        if 'review.json' in decoded:validate_review(decoded['review.json'],project_id)
        if 'studio.json' in decoded:StudioStore(directory,project_id).load()
    if art:
        images=[i for c in art['characters'] for i in c['references']]+[i for e in art['panels'].values() for i in e['candidates']]
        declared={i['path'] for i in images}
        if {p for p in files if p.startswith('images/')}!=declared:raise ValueError('人物参照・候補画像と保存ファイルの対応が一致しません。')
        for image in images:
            with decode_image(files[image['path']]) as source:
                if source.size!=(image['width'],image['height']):raise ValueError('画像の実寸が作画データと一致しません。')
        story_versions={v['id']:v['story'] for v in history} if history else {manifest['story_version_id']:normalized}
        for entry in art['panels'].values():
            for source in ([entry['direction']] if entry['direction'] else [])+entry['candidates']:
                version=story_versions.get(source['story_version_id'])
                if version is None:raise ValueError('画像・作画指示に対応するストーリー版がありません。')
                try:panel=version['pages'][entry['page']-1]['panels'][entry['panel']-1]
                except IndexError:raise ValueError('画像・作画指示の対象コマがありません。') from None
                if panel!=source['source_panel']:raise ValueError('作画指示・画像と脚本のコマ内容が一致しません。')
    return decoded


def validate_payload(files,project_id,manifest):
    try:return _validate_payload(files,project_id,manifest)
    except (KeyError,TypeError,AttributeError,IndexError) as error:
        raise ValueError('同期JSONの必須項目・型・人物と画像の対応が不正です。正式データは変更していません。') from error
