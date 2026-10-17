"""GitHub上の既存作品資料を候補用の標準形式へ変換する。採用は行わない。"""
import copy
from uuid import UUID,uuid5
from manga.artwork_models import empty_artwork,character_fields,direction_fields,panel_key
from manga.models import validate_id,now_iso
from manga.story import normalize_story
from manga.images import prepare_image
from manga.sync_manifest import json_bytes,decode_json,sha256


def _legacy_bundle(project_id,commit,story_content,artwork_content=None,images=None):
    raw=decode_json(story_content)
    if raw.get('schema_version')!=1:raise ValueError('既存ストーリーはschema_version: 1で指定してください。')
    story=normalize_story(raw).story
    version=str(uuid5(UUID(project_id),f'story:{sha256(json_bytes(story))}'))
    files={'story.json':json_bytes(story)}
    art=empty_artwork(project_id)
    if artwork_content is not None:
        values=decode_json(artwork_content)
        if values.get('project_id')!=project_id or values.get('schema_version')!=1:raise ValueError('作画提案の作品IDが一致しません。')
        art['style']=values['style']
        for c in values['characters']:
            art['characters'].append(dict(character_fields(c),id=validate_id(c['id']),created_at=now_iso(),updated_at=now_iso(),deleted_at=None,references=[],primary_reference_id=None))
        for key,value in values['panels'].items():
            n,c=value['page'],value['panel']
            if key!=panel_key(n,c):raise ValueError('作画指示のページ・コマ番号が一致しません。')
            try:source=story['pages'][n-1]['panels'][c-1]
            except IndexError:raise ValueError('作画指示の対象がストーリーにありません。') from None
            if value.get('source_description')!=source['description']:raise ValueError('作画指示の元描写が対象脚本と一致しません。')
            art['panels'][key]=dict(page=n,panel=c,direction=dict(direction_fields(value['fields']),story_version_id=version,source_panel=copy.deepcopy(source),updated_at=now_iso()),candidates=[],selected_image_id=None,selection_history=[])
        files['attachments/original_artwork_proposal.json']=artwork_content
    for item in images or []:
        n,c=item['page'],item['panel'];key=panel_key(n,c)
        try:source=story['pages'][n-1]['panels'][c-1]
        except IndexError:raise ValueError('画像の対象コマがありません。') from None
        content,width,height=prepare_image(item['content'])
        entry=art['panels'].setdefault(key,dict(page=n,panel=c,direction=None,candidates=[],selected_image_id=None,selection_history=[]))
        if entry['candidates']:raise ValueError('同じコマへの画像割当が重複しています。')
        direction=entry['direction']
        context=dict(style=art['style'],direction=copy.deepcopy(direction),characters=[copy.deepcopy(ch) for ch in art['characters'] if direction and ch['id'] in direction['character_ids']])
        identity=dict(story_version=version,panel=source,style=art['style'],direction=direction_fields(direction or {}),
                      characters=[dict(character_fields(ch),id=ch['id']) for ch in context['characters']])
        identifier=str(uuid5(UUID(project_id),f'{key}:{sha256(content)}:{sha256(json_bytes(identity))}'))
        path=f'images/panels/{identifier}.png';files[path]=content
        image=dict(id=identifier,path=path,original_name=item['path'].split('/')[-1],width=width,height=height,saved_at=now_iso(),story_version_id=version,source_panel=copy.deepcopy(source),context=context)
        entry['candidates'].append(image)
        # 同期元の推奨採用として記録するだけ。ローカル反映には別の確認操作が必要。
        entry['selected_image_id']=identifier
    files['artwork.json']=json_bytes(art)
    return files,version


def legacy_bundle(project_id,commit,story_content,artwork_content=None,images=None):
    try:return _legacy_bundle(project_id,commit,story_content,artwork_content,images)
    except (KeyError,TypeError,AttributeError,IndexError) as error:
        raise ValueError('作品提案JSONの必須項目・型・ページとコマの対応を確認してください。') from error
