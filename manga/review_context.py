"""品質確認の読み取り専用スナップショット。画像はstatのみで変更を検知する。"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from manga.artwork_storage import ArtworkStore
from manga.page_storage import PageStore
from manga.studio_storage import StudioStore
from manga.story_storage import StoryStore
from manga.storage import StorageError
from manga.ui_data import read_store, signature
from manga.japanese_text import find_japanese_font, TextLayoutError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class ReviewContext:
    project_id: str
    root: Path
    latest: dict | None
    artwork: dict | None
    composition: dict | None
    studio: dict | None
    errors: list
    snapshot: dict

    @property
    def pages(self):
        return self.latest['story']['pages'] if self.latest else []


def load_context(directory, project_id, *, cached=True):
    stores = {'story': StoryStore(directory, project_id), 'artwork': ArtworkStore(directory, project_id),
              'composition': PageStore(directory, project_id), 'studio': StudioStore(directory, project_id)}
    values, errors, stamps = {}, [], {}
    for name, store in stores.items():
        try:
            stamps[name] = signature(store.path)
            values[name] = read_store(store) if cached else (store.load_history() if name == 'story' else store.load())
            if signature(store.path) != stamps[name]:
                raise StorageError('読み込み中にデータが更新されました。再読み込みしてください。')
        except StorageError as error:
            values[name] = None
            errors.append(dict(source=name, message=str(error)))
    latest = values['story'][-1] if values['story'] else None
    art, composition = values['artwork'], values['composition']
    root = Path(directory) / project_id
    files = {}
    if art:
        images = [i for entry in art['panels'].values() for i in entry['candidates'] if i['id'] == entry['selected_image_id']]
        images += [i for c in art['characters'] for i in c['references']]
        for image in images:
            try:
                files[image['id']] = signature(stores['artwork'].image_path(image))
            except StorageError as error:
                files[image['id']] = dict(error=str(error))
    font_stamp = None
    if composition:
        try:
            font_stamp = find_japanese_font(composition['settings']['font_path']).signature
        except TextLayoutError as error:
            font_stamp = str(error)
    page_stamps = {}
    for page in latest['story']['pages'] if latest else []:
        entries = {str(p['panel']): art['panels'].get(f"{page['page']}:{p['panel']}") for p in page['panels']} if art else None
        adopted = [e['selected_image_id'] for e in (entries or {}).values() if e and e['selected_image_id']]
        # 候補追加や別ページの編集では、このページの目視確認を無効にしない。
        relevant = {key: dict(direction=e['direction'], selected_image=next((i for i in e['candidates'] if i['id'] == e['selected_image_id']), None)) if e else None for key, e in (entries or {}).items()}
        raw = dict(story_version_id=latest['id'], page=page, artwork=relevant,
                   characters=art['characters'] if art else None, style=art['style'] if art else None,
                   composition=composition['pages'].get(str(page['page'])) if composition else None,
                   settings=composition['settings'] if composition else None,
                   assets={i: files[i] for i in adopted}, font=font_stamp, errors=errors)
        page_stamps[str(page['page'])] = dict(fingerprint=digest(raw), story_version_id=latest['id'],
            artwork_revision=art['revision'] if art else None, composition_revision=composition['revision'] if composition else None,
            image_ids=adopted, direction_digest=digest(relevant), composition_digest=digest(raw['composition']))
    snapshot = dict(token=digest(dict(files=files, sources={k:v for k,v in stamps.items() if k != 'studio'}, font=font_stamp, errors=errors)),
                    story_version_id=latest['id'] if latest else None, pages=page_stamps)
    return ReviewContext(project_id, root, latest, art, composition, values['studio'], errors, snapshot)


def page_current(record, snapshot, page):
    key = str(page)
    return bool(record and key in snapshot['pages'] and
                record['snapshot']['pages'].get(key, {}).get('fingerprint') == snapshot['pages'][key]['fingerprint'])
