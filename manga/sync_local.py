"""選択した1作品だけを収集する。data/projects全体や秘密設定は走査しない。"""
from pathlib import Path
from manga.models import validate_id
from manga.storage import ProjectStore
from manga.story_storage import StoryStore
from manga.artwork_storage import ArtworkStore
from manga.page_storage import PageStore
from manga.editorial_storage import EditorialStore
from manga.assistant_repository import AssistantRepository
from manga.review_storage import ReviewStore
from manga.studio_storage import StudioStore
from manga.sync_manifest import check_limits,json_bytes,decode_json,sha256,MAX_FILE_BYTES,MAX_TOTAL_BYTES


def collect_local(directory,project_id):
    directory=Path(directory);root=directory/validate_id(project_id);files={}
    project_path=directory/f'{project_id}.json'
    if project_path.exists():files['project.json']=json_bytes(ProjectStore(directory).load(project_id).to_dict())
    stores=[StoryStore(directory,project_id),ArtworkStore(directory,project_id),PageStore(directory,project_id),
            EditorialStore(directory,project_id),AssistantRepository(directory,project_id),ReviewStore(directory,project_id),StudioStore(directory,project_id)]
    for store in stores:
        if store.path.exists():
            (store.load_history if isinstance(store,StoryStore) else store.load)()
            files[store.path.name]=json_bytes(decode_json(store.path.read_bytes()))
    if 'story_history.json' in files:files['story.json']=json_bytes(decode_json(files['story_history.json'])['versions'][-1]['story'])
    artwork=ArtworkStore(directory,project_id)
    art=artwork.load()
    images=[i for c in art['characters'] for i in c['references']]+[i for e in art['panels'].values() for i in e['candidates']]
    total=sum(map(len,files.values()))
    for image in images:
        path=artwork.image_path(image)
        size=path.stat().st_size
        if size>MAX_FILE_BYTES or total+size>MAX_TOTAL_BYTES:raise ValueError('画像の容量が同期上限を超えます。既存画像は削除せず、同期対象を見直してください。')
        files[image['path']]=artwork.image_bytes(image);total+=size
    if files:check_limits(files)
    return files


def fingerprint(files):
    return sha256(json_bytes({name:sha256(content) for name,content in sorted(files.items())}))


def manifest_context(files):
    art=decode_json(files['artwork.json']) if 'artwork.json' in files else None
    versions={}
    for name in ('artwork','composition','editorial','assistant','review','studio'):
        if f'{name}.json' in files:versions[name]=decode_json(files[f'{name}.json'])['revision']
    characters=[];adoptions=[]
    if art:
        for c in art['characters']:
            characters.append(dict(id=c['id'],primary_reference_id=c['primary_reference_id'],references=[dict(id=i['id'],sha256=sha256(files[i['path']])) for i in c['references']]))
        for e in art['panels'].values():
            adoptions.append(dict(page=e['page'],panel=e['panel'],image_id=e['selected_image_id'],direction_version=(e['direction'] or {}).get('story_version_id')))
    return dict(settings_versions=versions,characters=characters,adoptions=adoptions)
