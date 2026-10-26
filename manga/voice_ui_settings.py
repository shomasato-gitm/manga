"""人物IDに声を割り当てる。料金表は利用者の公式確認を必要とする。"""
import json

from manga.ui_runtime import ui as st
from manga.editorial_ui_common import json_input,input_text
from manga.story import load_json_payload
from manga.voice_models import MOCK_VOICES,POLLY_VOICES,POLLY_ENGINES,EMOTIONS,voice_settings
from manga.voice_pricing import pricing_template
from manga.voice_ui import act


def settings_view(service,data,plan,prefix):
    from manga.voice_ui_local import connection_view,local_settings,PROVIDERS
    connection_view(prefix)
    st.subheader('人物別の声設定')
    st.caption('確認用モックは日本語の人声ではなくトーンです。Pollyの感情は速度・音量による演技指定で、感情専用モデルの品質を保証しません。')
    st.caption('Standardのささやきは専用SSMLタグ、Neuralのささやきは小声・低速の近似です。怒り・悲しみも演技プリセットとして試聴確認してください。')
    voices={}
    for index,(identifier,name) in enumerate(service.speakers(plan['artwork']).items()):
        existing=data['voices'].get(identifier,voice_settings(dict(voice_id=MOCK_VOICES[index%4])))
        with st.expander(name,expanded=index==0):
            key=f'{prefix}_{identifier}'
            provider=st.selectbox(name+'のプロバイダー',list(PROVIDERS),index=list(PROVIDERS).index(existing['provider']),format_func=PROVIDERS.get,key=key+'_provider')
            if provider in ('manual-wav','voicevox'):
                voices[identifier]=local_settings(provider,existing,key,prefix)
                continue
            if provider=='mock':
                engine='mock'
                current=existing['voice_id'] if existing['voice_id'] in MOCK_VOICES else MOCK_VOICES[index%4]
                voice=st.selectbox(name+'の確認用トーン',MOCK_VOICES,index=MOCK_VOICES.index(current),key=key+'_mock_voice')
            else:
                engines=('standard','neural')
                engine=st.selectbox(name+'のPollyエンジン',engines,index=engines.index(existing['engine']) if existing['engine'] in engines else 1,key=key+'_engine')
                choices=[v for v in POLLY_VOICES if engine in POLLY_ENGINES[v]]
                old_voice=existing['voice_id'] if existing['voice_id'] in choices else 'Takumi'
                voice=st.selectbox(name+'のPolly声ID',choices,index=choices.index(old_voice),key=key+'_'+engine+'_aws_voice')
                st.caption('声ID・engine・日本語対応と利用条件は公式資料で確認してください。選択だけではAPIを呼び出しません。')
            speed=st.slider(name+'の速度',.5,1.5,value=existing['speed'],step=.05,key=key+'_speed')
            emotion=st.selectbox(name+'の基本感情',list(EMOTIONS),index=list(EMOTIONS).index(existing['emotion']),format_func=EMOTIONS.get,key=key+'_emotion')
            voices[identifier]=dict(provider=provider,engine=engine,voice_id=voice,speed=speed,emotion=emotion)
    incomplete=any(v is None for v in voices.values())
    if incomplete:st.info('VOICEVOXの話者一覧を取得・選択するか、WAV手動取り込みを選択してください。')
    if st.button('人物別の声設定を保存',type='primary',disabled=incomplete):
        act(lambda:service.save_voices(voices,expected_revision=data['revision']),'人物IDを保ったまま声設定を保存しました。')
    with st.expander('有料TTSの料金・利用条件（公式確認後に設定）'):
        st.warning('有料APIは別途の明示承認まで実行しません。料金設定だけで有効にはなりません。')
        st.download_button('未記入の料金設定JSONを取得',json.dumps(pricing_template(),ensure_ascii=False,indent=2),file_name='tts_pricing_template.json',mime='application/json')
        values=json_input('TTS料金設定JSON',prefix+'_pricing')
        checked=st.checkbox('公式の最新料金・商用利用条件・日本語対応・最大請求量を確認した',key=prefix+'_pricing_review')
        if st.button('確認済み料金設定を保存',disabled=not checked):
            act(lambda:service.save_pricing(load_json_payload(input_text(values)),expected_revision=data['revision']),'確認済み料金設定を保存しました。有料呼び出しは別途承認が必要です。')
        if data['pricing'] and st.checkbox('保存した料金設定の詳細を表示',key=prefix+'_pricing_details'):st.json(data['pricing'],expanded=False)
