"""日本語音声の明示操作。画面描画・作品切替だけでは接続しない。"""
import json

from manga.ui_runtime import ui as st
from manga.storage import StorageError
from manga.voice_models import voice_settings
from manga.voice_ui import act
from manga.voicevox import Voicevox,ORIGINS,MAC_NOTICE,SOFTWARE_TERMS,local_available

PROVIDERS={'mock':'無料モック（確認用トーン）','manual-wav':'WAV手動取り込み','voicevox':'VOICEVOX（このMac）','aws-polly':'Amazon Polly（有料・既定OFF）'}


def connection_view(prefix):
    with st.expander('ローカルVOICEVOXの準備・利用可否'):
        st.info(MAC_NOTICE)
        st.caption('接続先はこのアプリを動かすMacです。別PC・クラウドで起動したアプリから、利用者のMacには接続できません。')
        st.markdown('[公式ENGINE配布](https://github.com/VOICEVOX/voicevox_engine/releases) ｜ [ソフト規約](https://voicevox.hiroshiba.jp/term/) ｜ [各音声の規約案内](https://voicevox.hiroshiba.jp/)')
        if not local_available():
            st.warning('ローカル接続は無効です。Mac上で MANGA_ENABLE_LOCAL_VOICEVOX=1 を指定してアプリを起動するか、WAV手動取り込みを選択してください。')
        origin=st.selectbox('ローカルエンジン接続先',ORIGINS,key=prefix+'_local_origin')
        consent=st.checkbox('このMacのエンジンへ接続し、稼働状況・話者一覧を取得する',key=prefix+'_discover_consent')
        if st.button('ローカルエンジンと話者を確認',disabled=not consent or not local_available(),key=prefix+'_discover'):
            try:
                catalog=Voicevox(origin).discover(consent=consent)
                st.session_state[prefix+'_catalog']=catalog
                st.success('ローカルエンジンの話者一覧を取得しました。')
            except (ValueError,OSError) as error:
                st.session_state.pop(prefix+'_catalog',None)
                st.error(str(error))
        catalog=st.session_state.get(prefix+'_catalog')
        if catalog:st.caption(f"取得済みエンジン：{catalog['version']} ／ {catalog['origin']}（生成直前にも話者対応を照合します）")


def local_settings(provider,existing,key,prefix):
    if provider=='manual-wav':
        st.caption('この人物の各発話にWAVを取り込みます。読み・速度・演技は元ファイルに含まれます。')
        return voice_settings(dict(provider=provider,engine='manual',voice_id='manual'))
    catalog=st.session_state.get(prefix+'_catalog')
    if not catalog:
        st.info('上の「ローカルエンジンと話者を確認」で取得してください。保存済みの対応付けは保持します。')
        return existing if existing['provider']=='voicevox' else None
    options={(s['speaker_uuid'],style['id']):f"{s['name']}／{style['name']}" for s in catalog['speakers'] for style in s['styles']}
    previous=(existing['voice_id'],existing.get('style_id'))
    selected=st.selectbox('この人物に使う話者・スタイル',list(options),index=list(options).index(previous) if previous in options else 0,
                          format_func=options.get,key=key+'_local_style')
    speed=st.slider('話速',.5,1.5,float(existing.get('speed',1)),.05,key=key+'_local_speed')
    pitch=st.slider('音高',-.15,.15,float(existing.get('pitch',0)),.01,key=key+'_local_pitch')
    intonation=st.slider('抑揚',0.,2.,float(existing.get('intonation',1)),.05,key=key+'_local_intonation')
    volume=st.slider('音量',0.,2.,float(existing.get('volume',1)),.05,key=key+'_local_volume')
    st.caption('漫画の人物と音声ライブラリの話者は別のIDです。音声のスタイルで演技を指定し、試聴して確認します。')
    return voice_settings(dict(provider='voicevox',engine='local',voice_id=selected[0],style_id=selected[1],origin=catalog['origin'],
                               speed=speed,pitch=pitch,intonation=intonation,volume=volume))


def rights_form(key,provider):
    st.caption('ソフト規約と音声ライブラリ固有の規約は別々に確認してください。自分の録音でも、話者の許諾・公開範囲を記録します。')
    software=st.text_input('ソフトの利用規約URL・根拠',value=SOFTWARE_TERMS if provider=='voicevox' else '',key=key+'_software')
    library=st.text_input('音声ライブラリ・録音の利用条件URL・許諾根拠',key=key+'_library')
    source=st.text_input('出典・話者名・取り込み元',key=key+'_source')
    credit=st.text_input('必要なクレジット（不要なら根拠を記入）',key=key+'_credit')
    software_ok=st.checkbox('ソフトの利用条件を確認した',key=key+'_software_ok')
    library_ok=st.checkbox('音声固有の商用利用・配信条件と必要なクレジットを確認した',key=key+'_library_ok')
    return dict(source=source,software_terms=software,library_terms=library,credit=credit,software_confirmed=software_ok,library_confirmed=library_ok)


def local_generation_view(service,data,plan,row,prefix):
    provider=row['settings']['provider']
    key=f'{prefix}_{row["id"]}_{row["cache_key"]}_{plan["version"]["id"]}_local'
    st.subheader('日本語音声の準備')
    st.write('生成方式：'+PROVIDERS[provider])
    st.caption('候補を保存 → 試聴・権利を確認 → 正式割り当て → 字幕・タイムライン')
    st.write(f"{row['page']}ページ {row['panel']}コマ ／ {row['speaker_name']}：{row['spoken_text']}")
    if row['needs_review']:
        st.warning('上の発話設定で話者と読みを確認・保存してください。');return
    target=service.voice_target(row['id'])
    with st.expander('1発話の候補を準備する',expanded=not bool(service.local_candidates(row['id']))):
        rights=rights_form(key,provider)
        if provider=='manual-wav':
            st.caption('取り違え防止のため、この発話の対象確認票を保存し、WAVと一緒に選択してください。録音内容の一致は試聴で確認します。')
            st.download_button('音声の対象確認票を取得',json.dumps(target,ensure_ascii=False,indent=2),file_name=f"voice-target-{row['id']}.json",mime='application/json',key=key+'_receipt_download')
            wav=st.file_uploader('この発話のWAV',type=['wav'],key=key+'_wav')
            receipt=st.file_uploader('音声の対象確認票JSON',type=['json'],key=key+'_receipt')
            if st.button('WAVを候補として取り込む',type='primary',disabled=wav is None or receipt is None,key=key+'_import'):
                def upload():
                    if receipt.size>64*1024:raise ValueError('対象確認票が大きすぎます。現在の発話から取得し直してください。')
                    try:target_uploaded=json.loads(receipt.getvalue())
                    except (ValueError,UnicodeError):raise ValueError('対象確認票を読み込めません。現在の発話から取得し直してください。') from None
                    return service.import_wav(row['id'],wav.getvalue(),target=target_uploaded,rights=rights,expected_revision=data['revision'])
                act(upload,'WAVを候補として保存しました。試聴・確認後に正式割り当てしてください。')
        else:
            st.info(MAC_NOTICE)
            st.caption('送る情報：この1発話の読み上げ文、話者・スタイル、話速等の設定。作品・画像・基準画像は送りません。')
            consent=st.checkbox('この1発話を、このMacのVOICEVOXで生成する',key=key+'_consent')
            regenerate=st.checkbox('保存済み候補を残して新しく生成する',key=key+'_regenerate')
            if not local_available():st.warning('Macのローカル接続は無効です。「準備・話者設定」で接続条件を確認するか、WAV手動取り込みへ切り替えてください。')
            if st.button('1発話のプレビュー音声を生成・保存',type='primary',disabled=not consent or not local_available(),key=key+'_generate'):
                act(lambda:service.preview_voicevox(row['id'],target=target,rights=rights,expected_revision=data['revision'],consent=consent,regenerate=regenerate),
                    '日本語音声を候補として保存しました。試聴・確認後に正式割り当てしてください。')
    candidates={a['id']:a for a in reversed(service.local_candidates(row['id']))}
    if not candidates:
        st.info('まだ候補がありません。WAV取り込みまたは1発話生成から始めてください。');return
    identifier=st.selectbox('試聴・比較する音声候補',list(candidates),format_func=lambda a:f"{candidates[a]['created_at']} ／ {candidates[a]['info']['duration_ms']/1000:.2f}秒 ／ {a[:8]}",key=key+'_candidate')
    asset=candidates[identifier]
    try:
        content=service.audio_bytes(asset)
        st.audio(content,format='audio/wav')
        st.download_button('候補WAVを保存',content,file_name=identifier+'.wav',mime='audio/wav',key=key+'_download')
        st.write('クレジット：'+asset['rights']['credit'])
        checked=data['audio_reviews'].get(identifier,{}).get('sha256')==asset['info']['sha256']
        heard=st.checkbox('この候補を試聴し、人物・日本語の読み・内容・音量を確認した',key=key+'_'+identifier+'_heard')
        if st.button('音声の試聴確認を記録',disabled=not heard or not asset['provenance']['rights']['confirmed'],key=key+'_review'):
            act(lambda:service.review_audio(identifier,expected_revision=data['revision']),'試聴確認を記録しました。次に正式割り当てしてください。')
        if not asset['provenance']['rights']['confirmed']:st.warning('利用条件が未確認です。規約を確認し、新しい候補として取り込んでください。')
        if not checked:st.caption('正式割り当てには、この音声候補の試聴確認が必要です。画像の確認とは別に記録します。')
        if st.button('この発話へ正式割り当て',type='primary',disabled=not checked,key=key+'_assign'):
            act(lambda:service.select_asset(row['id'],identifier,expected_revision=data['revision']),'正式割り当てしました。音声を変更した場合は字幕・タイムラインを新しい版で保存し、動画を再確認してください。')
        if data['assignments'].get(row['id'])==identifier and checked:st.success('この発話へ正式割り当て済みです。')
        with st.expander('元WAV・対象・SHA・利用条件の詳細'):
            from manga.voice_local import original_bytes
            st.download_button('変換前の元WAVを保存',original_bytes(service,asset),file_name=identifier+'-original.wav',mime='audio/wav',key=key+'_original')
            st.json(asset,expanded=False)
    except (ValueError,OSError,StorageError) as error:st.error(str(error))
