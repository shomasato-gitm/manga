"""固定設定と例外を一人ずつ確認する。画像は明示選択した一枚だけ読み込む。"""
from manga.character_ai_assets import reference_path, references
from manga.character_ai_models import FIXED_FIELDS, VARIABLE_ELEMENTS
from manga.character_ai_service import bible_stale, latest_bibles
from manga.character_ai_ui import action
from manga.ui_runtime import ui as st
from manga.voice_models import digest


def bible_editor(service, version_id, data, enabled, prefix, navigate):
    art = service.artwork.load()
    people = {c['id']:c for c in art['characters'] if not c['deleted_at']}
    if not people: st.info('先に人物を正式採用または手動登録してください。'); return
    identifier = st.selectbox('バイブルを確認する人物', list(people), format_func=lambda i:people[i]['name']+' / '+i[:8], key=prefix+'_bible_person')
    person = people[identifier]
    current = latest_bibles(data).get(identifier)
    key = prefix+'_'+identifier+'_bible_'+str(current['id'] if current else 'new')
    refs = references(service.store.root, person)
    if current and bible_stale(current, service.proposals.latest(), person, refs):
        st.warning('バイブルは古い状態です。元の人物設定・脚本・参照画像の変更を再確認してください。')
    st.subheader(person['name']+'の固定設定')
    st.dataframe([dict(項目=label, 現在の正式設定=person.get(k, '') or '未指定・要確認', 前のバイブル=current['profile'].get(k, '') if current else '') for k,label in FIXED_FIELDS.items()], hide_index=True)
    st.caption('場面で変化する要素：'+'・'.join(VARIABLE_ELEMENTS)+'。衣装・成長・回想等の変更は対象コマ付きの例外として確認します。')
    options = [k for k in FIXED_FIELDS if person.get(k)]
    default = [k for k in (current['locked_fields'] if current else options) if k in options]
    locked = st.multiselect('固定してロックする項目', options, default=default, format_func=lambda k:FIXED_FIELDS[k], key=key+'_locks')
    st.caption('ロックはこの人物提案の採用を保護します。従来の手動編集で変更された場合はバイブルと検査が古い状態になります。')
    aliases = st.text_area('確認済みの別名・呼称（任意・1行1名）', value='\n'.join(current['aliases']) if current else '', max_chars=3000, key=key+'_aliases')
    exceptions = current['exceptions'].copy() if current else []
    if exceptions:
        with st.expander('現在の承認済み場面例外'): st.json(exceptions, expanded=False)
        if st.checkbox('この新しいバイブルでは場面例外をすべて解除（旧版は保持）', key=key+'_clear'): exceptions = []
    with st.expander('衣装変更・回想・成長などの場面例外を追加（任意）'):
        add = st.checkbox('今回の確認と同時に例外を追加する', key=key+'_add')
        if add:
            targets = [f"{p['page']}:{c['panel']}" for p in service.proposals.latest()['story']['pages'] for c in p['panels']]
            selected = st.multiselect('例外を許可するページ・コマ', targets, key=key+'_targets')
            field = st.selectbox('例外で変更する特徴', list(FIXED_FIELDS), format_func=lambda k:FIXED_FIELDS[k], key=key+'_field')
            value = st.text_input('例外で使う設定', max_chars=2000, key=key+'_value')
            why = st.text_area('例外の理由（衣装替え・回想など）', max_chars=2000, key=key+'_why')
            exceptions.append(dict(targets=[dict(page=int(t.split(':')[0]), panel=int(t.split(':')[1])) for t in selected], changes={field:value}, reason=why))
    st.subheader('基準画像・参照画像の状態')
    st.write('採用基準画像ID：'+(person['primary_reference_id'] or '未登録・未完了'))
    if not refs: st.warning('基準画像が未登録です。画像の外見は未確認です。')
    else:
        st.dataframe([dict(ID=r['id'], 種類=r['view'], 状態='登録あり（外見未検査）' if r['state']=='present' else 'ファイル欠損', 採用基準=r['id']==person['primary_reference_id']) for r in refs], hide_index=True)
        if st.checkbox('参照画像を1枚だけ表示・取得する', key=key+'_show'):
            by_id = {r['id']:r for r in person['references']}
            selected = st.selectbox('表示する参照画像', list(by_id), format_func=lambda i:by_id[i]['view']+' / '+i[:8], key=key+'_image')
            path = reference_path(service.store.root, by_id[selected])
            if path.is_file():
                from manga.gallery_ui import thumbnail
                from PIL import Image, UnidentifiedImageError
                try:
                    st.image(thumbnail(service.artwork, by_id[selected]), caption='外見は利用者が目視で確認してください。')
                except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                    st.error('参照画像を正常に表示できません。元ファイルを保持し、バックアップまたは画像形式を確認してください。')
                st.download_button('選択した参照画像をダウンロード', service.artwork.image_bytes(by_id[selected]), file_name=path.name, mime='image/png')
            else: st.warning('参照画像ファイルが欠損しています。')
    reason = st.text_area('今回の確認・採用根拠（必須）', max_chars=2000, key=key+'_reason')
    consent = digest(dict(locks=locked, aliases=aliases, exceptions=exceptions, reason=reason, art=art['revision'], revision=data['revision']))
    confirmed = st.checkbox('固定設定・別名・参照状態・場面例外を確認した', key=key+'_'+consent+'_confirmed')
    if st.button('確認済みバイブルを新しい版として保存', type='primary', disabled=not enabled or not confirmed or not reason.strip()):
        action(lambda:service.save_bible(identifier, version_id=version_id, expected_revision=data['revision'], expected_artwork_revision=art['revision'], locked_fields=locked, aliases=[n.strip() for n in aliases.splitlines() if n.strip()], exceptions=exceptions, reason=reason, confirmed=confirmed), 'バイブルを新しい版として保存しました。基準画像の未登録・欠損は未完了のまま表示します。')
    if navigate and st.button('既存の基準画像登録・作風設定へ'): navigate('キャラクター・作風', '基準画像・作風')
