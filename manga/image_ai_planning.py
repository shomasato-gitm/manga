"""確定脚本とバイブルから対象だけの画像計画を作る。全コマの暗黙生成はしない。"""
import hashlib

from manga.artwork_models import character_fields, panel_key
from manga.character_ai_checks import inspect
from manga.character_ai_service import CharacterAIService, latest_bibles, bible_stale
from manga.image_ai_models import settings
from manga.page_storage import PageStore
from manga.voice_models import digest


def target(kind,character_id=None,page=None,panel=None,view='正面',expression='自然な表情'):
    from manga.artwork_models import text
    from manga.models import validate_id
    if kind=='character':
        return dict(kind=kind,character_id=validate_id(character_id),page=None,panel=None,view=text(view,'基準画像の種類',100,True),expression=text(expression,'表情',500))
    if kind!='panel': raise ValueError('基準画像またはコマ画像を選択してください。')
    panel_key(page,panel)
    return dict(kind=kind,character_id=None,page=page,panel=panel,view=None,expression=None)


def materials(service,selection,*,hashed=False):
    snapshot=service.snapshot(hashed=hashed); latest=snapshot['latest']; art=snapshot['artwork']
    service.proposals.finalized(latest['id'])
    bibles=latest_bibles(snapshot['data']); people={c['id']:c for c in art['characters'] if not c['deleted_at']}
    if selection['kind']=='character':
        ids=[selection['character_id']]; source=None; direction=None; layout=None
    else:
        page,panel=selection['page'],selection['panel']
        try: source=latest['story']['pages'][page-1]['panels'][panel-1]
        except IndexError: raise ValueError('対象コマが脚本にありません。') from None
        direction=(art['panels'].get(panel_key(page,panel)) or {}).get('direction')
        if not direction or direction['story_version_id']!=latest['id'] or direction['source_panel']!=source: raise ValueError('最新の作画指示を先に保存してください。')
        ids=direction['character_ids']
        composition=PageStore(service.store.root.parent,service.project_id).load()
        layout=dict(settings=composition['settings'],page=composition['pages'].get(str(page)))
        blocking={'fixed_conflict','name_id_mismatch','feature_identity_ambiguous','unknown_character_id','direction_stale','speaker_unconfirmed','speaker_direction_mismatch'}
        problems=[i for i in inspect(snapshot) if (i['page'],i['panel'])==(page,panel) and i['kind'] in blocking]
        if problems: raise ValueError('人物・話者・固定設定を確認してください：'+' / '.join(i['message'] for i in problems[:3]))
    selected=[]
    for identifier in ids:
        person=people.get(identifier); bible=bibles.get(identifier); refs=snapshot['references'].get(identifier)
        if not person or not bible or refs['error'] or bible_stale(bible,latest,person,refs['images']): raise ValueError('人物・バイブル・参照画像が未確認または古い状態です。')
        from manga.character_ai_extraction import matches
        aliases={i:b['aliases'] for i,b in bibles.items()}
        if len(matches(person['name'],list(people.values()),aliases))!=1: raise ValueError('同名・別名の照合候補が重複しています。人物の同定を先に整理してください。')
        if any(r['state']!='present' for r in refs['images']): raise ValueError('参照画像が欠損しています。バイブルで確認してください。')
        if selection['kind']=='panel' and (not person['primary_reference_id'] or not any(r['id']==person['primary_reference_id'] for r in refs['images'])):
            raise ValueError('コマ画像の前に人物の基準画像を採用し、バイブルを確認してください。')
        selected.append(dict(id=identifier,profile=character_fields(person),bible_id=bible['id'],primary_reference_id=person['primary_reference_id'],
            references=[{k:v for k,v in r.items() if k!='sha256'} for r in refs['images']]))
    from manga.storage import ProjectStore
    project=ProjectStore(service.store.root.parent).load(project_id=service.project_id).to_dict()
    state=dict(project={k:project[k] for k in ('title','genre','synopsis','page_count')},story_version_id=latest['id'],story_sha256=digest(latest['story']),selection=selection,style=art['style'],characters=selected,source_panel=source,direction=direction,layout=layout)
    return snapshot,state,digest(state)


def create_plan(directory,project_id,selection,options=None):
    service=CharacterAIService(directory,project_id)
    before,state,token=materials(service,selection,hashed=True)
    arguments={k:v for k,v in selection.items() if k!='kind' and v is not None}
    package=service.prompt(selection['kind'],**arguments)
    _,_,after=materials(service,selection,hashed=False)
    if after!=token: raise ValueError('計画作成中に元設定が変わりました。作成し直してください。')
    config=settings(options)
    refs=package['reference_images']
    if len({r['id'] for r in refs})!=len(refs): raise ValueError('参照画像IDが重複しています。')
    from manga.video_sources import file_bytes
    from manga.images import prepare_image
    from PIL import Image
    from io import BytesIO
    for ref in refs:
        payload=file_bytes(service.store.root,ref['path'],20*1024*1024)
        if hashlib.sha256(payload).hexdigest()!=ref['sha256']: raise ValueError('参照画像のSHAが一致しません。')
        prepare_image(payload)
        with Image.open(BytesIO(payload)) as image:
            if image.format!='PNG': raise ValueError('正規の参照画像はPNGである必要があります。偽の拡張子を拒否しました。')
    prompt=package['prompt']+f"\n\n出力サイズ：{config['size']}。構図補足：{config['composition_note'] or '既存の指示を維持'}。\nフォーカス領域（正規化・中心と幅高さ）：{config['focus']}。余白：{config['margin']}。横型・縦型への転用時も顔・手・重要物を切らないようにしてください。"
    return dict(**selection,story_version_id=package['story_version_id'],material_token=token,prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                bible_ids=package['bible_ids'],reference_images=refs,options=config,warnings=package['warnings'],source=state,
                references_sent_to_external=False,external_api_calls=0)


def current(directory,project_id,plan,*,hashed=False):
    service=CharacterAIService(directory,project_id)
    selection={k:plan[k] for k in ('kind','character_id','page','panel','view','expression')}
    _,_,token=materials(service,selection,hashed=hashed)
    if token!=plan['material_token']: raise ValueError('元の脚本・人物・バイブル・参照・作画・配置が変わりました。古い候補は採用せず、新しい計画で確認してください。')
    if hashed:
        snapshot=service.snapshot(hashed=True)
        now={r['id']:r for v in snapshot['references'].values() for r in v['images']}
        for ref in plan['reference_images']:
            if ref['id'] not in now or ref['sha256']!=now[ref['id']]['sha256']: raise ValueError('基準画像のSHAが変わっています。送信・採用を停止しました。')
    return True
