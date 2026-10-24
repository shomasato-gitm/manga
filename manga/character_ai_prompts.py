"""既存画像プロンプトへ確認済みバイブル・場面例外・参照IDを補足する。通信なし。"""
import copy
import json

from manga.artwork_models import panel_key, text
from manga.character_ai_checks import inspect
from manga.character_ai_models import STATIC_NOTICE
from manga.character_ai_service import bible_stale, effective_profile, latest_bibles
from manga.image_prompts import character_image_prompt, panel_image_prompt
from manga.storage import ProjectStore


def build_prompt(service, kind, *, character_id=None, page=None, panel=None, view='正面', expression='自然な表情'):
    snapshot = service.snapshot(hashed=True)
    latest, art = snapshot['latest'], copy.deepcopy(snapshot['artwork'])
    service.proposals.finalized(latest['id'])
    bibles = latest_bibles(snapshot['data'])
    characters = {c['id']:c for c in art['characters'] if not c['deleted_at']}
    selected = []
    if kind == 'character':
        if character_id not in characters: raise ValueError('人物を選択してください。')
        selected = [character_id]
    elif kind == 'panel':
        key = panel_key(page, panel)
        entry = art['panels'].get(key)
        if not entry or not entry['direction'] or entry['direction']['story_version_id'] != latest['id']:
            raise ValueError('このコマの最新の作画指示を保存してください。')
        selected = entry['direction']['character_ids']
        blocked = [i for i in inspect(snapshot) if i['page'] == page and i['panel'] == panel and i['kind'] in ('fixed_conflict', 'name_id_mismatch', 'feature_identity_ambiguous', 'unknown_character_id', 'direction_stale')]
        if blocked: raise ValueError('固定特徴・人物ID・版の矛盾を先に解決してください：' + ' / '.join(i['message'] for i in blocked[:3]))
    else:
        raise ValueError('プロンプトの対象が不正です。')
    links, contexts, warnings = [], [], []
    for identifier in selected:
        character = characters.get(identifier)
        bible = bibles.get(identifier)
        assets = snapshot['references'].get(identifier)
        if character is None or bible is None or assets['error'] or bible_stale(bible, latest, character, assets['images']):
            raise ValueError('対象人物のバイブルが未確認・古い状態です。固定設定と参照画像を確認し直してください。')
        profile, applied = effective_profile(bible, page, panel)
        character.update(profile)
        contexts.append(dict(character_id=identifier, bible_id=bible['id'], locked_fields=bible['locked_fields'], fixed_profile=profile, approved_scene_exceptions=applied))
        if not assets['images']: warnings.append(character['name'] + '：基準画像未登録。外見の一致は未確認です。')
        for image in assets['images']:
            links.append(dict(character_id=identifier, primary=image['id'] == character['primary_reference_id'], **image))
            if image['state'] != 'present': warnings.append(character['name'] + '：参照画像ファイルが欠損しています。')
    if kind == 'character':
        base = character_image_prompt(characters[character_id], art['style'], text(view, '種類', 100, True), text(expression, '表情', 500))
    else:
        try: source_page = latest['story']['pages'][page-1]; source_panel = source_page['panels'][panel-1]
        except IndexError: raise ValueError('対象ページ・コマが脚本にありません。') from None
        from manga.page_storage import PageStore
        composition = PageStore(service.store.root.parent, service.project_id).load()
        base = panel_image_prompt(ProjectStore(service.store.root.parent).load(service.project_id), latest['story'], source_page, source_panel, art, composition)
    prompt = base + '\n\n確認済みキャラクターバイブル（このコマだけの例外以外は固定）：\n' + json.dumps(contexts, ensure_ascii=False, indent=2)
    prompt += '\n参照画像ID・SHA-256（画像自体は送信していません）：\n' + json.dumps(links, ensure_ascii=False, indent=2)
    prompt += '\n参照画像はユーザーがChatGPTへ実際に手動添付してください。欠損・未登録の場合は外見を確認したと判断せず、必要な確認を求めてください。\n' + STATIC_NOTICE
    return dict(kind=kind, character_id=character_id, page=page, panel=panel, story_version_id=latest['id'],
                source_token=snapshot['token'], bible_ids=[bibles[i]['id'] for i in selected], reference_images=links, warnings=warnings, prompt=prompt)
