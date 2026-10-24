"""独立した3人物・8ページ24コマの検証用素材。実ユーザーデータは使わない。"""
from manga.character_ai_samples import create_character_example
from manga.image_ai_service import ImageAIService


def confirm_bible(characters,identifier,*,exceptions=None):
    person=characters.artwork._character(characters.artwork.load(),identifier)
    from manga.character_ai_service import latest_bibles
    old=latest_bibles(characters.store.load()).get(identifier)
    return characters.save_bible(identifier,version_id=characters.proposals.latest()['id'],expected_revision=characters.store.load()['revision'],
        expected_artwork_revision=characters.artwork.load()['revision'],locked_fields=[k for k in ('hair_color','clothing','visual_identity') if person.get(k)],
        aliases=[],exceptions=exceptions if exceptions is not None else old['exceptions'] if old else [],reason='専用テスト作品の設定と参照状態を人間確認として明示',confirmed=True)


def create_image_example(directory):
    project,characters=create_character_example(directory)
    version=characters.proposals.latest()
    result=characters.proposals.create_mock(project,version['id'],expected_revision=0)
    original=characters.artwork.load()['characters'][0]
    for index,item in enumerate(result['proposal']['payload']['items']):
        characters.proposals.adopt_fields(result['proposal']['id'],item['id'],item['fields'],['visual_identity'] if index==0 else [k for k,v in item['fields'].items() if v],
            original['id'] if index==0 else None,expected_revision=characters.proposals.load()['revision'],expected_artwork_revision=characters.artwork.load()['revision'],
            expected_editorial_revision=characters.proposals.editorial.load()['revision'],expected_character_revision=characters.store.load()['revision'],identity_confirmed=True)
    cast={c['name']:c for c in characters.artwork.load()['characters']}
    for person in cast.values():
        confirm_bible(characters,person['id'],exceptions=[dict(targets=[dict(page=1,panel=2)],changes=dict(clothing='体操服'),reason='体育の回想場面')] if person['name']=='悠真' else [])
    for page in version['story']['pages']:
        for panel in page['panels']:
            person=cast[('悠真','美咲','蓮')[panel['panel']-1]]
            ids=[person['id']]
            labels=person['name']+'：'+ '、'.join(label+'='+person[k] for k,label in (('hair_color','髪色'),('clothing','服装'),('visual_identity','視覚的な差別化')) if person.get(k))
            if page['page']==1 and panel['panel']==2:
                ids.insert(0,original['id']); labels='悠真：髪色=黒、服装=体操服\n'+labels
            characters.artwork.save_direction(page['page'],panel['panel'],dict(character_ids=ids,composition=labels),story_version_id=version['id'],expected_revision=characters.artwork.load()['revision'])
    return project,ImageAIService(directory,project.id),characters
