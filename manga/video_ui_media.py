"""任意のBGM/SFX。登録・権利確認は明示操作で行う。"""
from manga.ui_runtime import ui as st
from manga.video_ui import action


def media(service,data,plan,key):
    with st.expander('BGM・効果音（任意／自分で用意した素材のみ）'):
        st.caption('WAV/MP3・20MiB・10分以内・48kHz以下。自動ダウンロードは行いません。元音声は変更しません。')
        uploaded=st.file_uploader('BGM/SFXファイル',type=['wav','mp3'],key=key+'_upload')
        rights=st.checkbox('この音声素材の利用・編集・配信の権利を確認した',key=key+'_audio_rights')
        if st.button('音声素材を履歴付きで登録',disabled=uploaded is None or not rights,key=key+'_import'):
            action(lambda:service.add_audio(uploaded.getvalue(),uploaded.name,rights_reviewed=rights,expected_revision=data['revision']),'音声素材を登録しました。BGMまたは効果音として選択してください。')
        assets={a['id']:a for a in data['audio']}
        choices=[None,*assets];existing=plan['bgm']['id'] if plan['bgm'] else None
        bgm=st.selectbox('BGM素材',choices,index=choices.index(existing) if existing in choices else 0,format_func=lambda i:assets[i]['name'] if i else '使用しない',key=key+'_bgm')
        if bgm:
            value=plan['bgm'] if plan['bgm'] and plan['bgm']['id']==bgm else dict(id=bgm,volume=.15,start_ms=0,fade_ms=1000)
            plan['bgm']=edit(value,key+'_bgm_'+bgm,'BGM')
        else:plan['bgm']=None
        sfx=st.selectbox('追加する効果音',choices,format_func=lambda i:assets[i]['name'] if i else '選択してください',key=key+'_sfx')
        if st.button('効果音を追加',disabled=sfx is None or len(plan['sfx'])>=20,key=key+'_sfx_add'):
            plan['sfx'].append(dict(id=sfx,volume=.3,start_ms=0,fade_ms=100));st.rerun()
        for i,item in enumerate(plan['sfx']):
            st.caption(f"効果音{i+1}：{assets.get(item['id'],{}).get('name','不明な素材')}")
            plan['sfx'][i]=edit(item,key+f'_sfx_{i}','効果音'+str(i+1))
            if st.button('この効果音の使用をやめる',key=key+f'_sfx_{i}_remove'):
                plan['sfx'].pop(i);st.rerun()


def edit(item,key,label):
    return dict(id=item['id'],volume=st.slider(label+'音量',0.0,1.0,value=float(item['volume']),step=.05,key=key+'_volume'),
        start_ms=st.number_input(label+'開始位置（ms）',min_value=0,max_value=600000,value=item['start_ms'],key=key+'_start'),
        fade_ms=st.number_input(label+'フェード（ms）',min_value=0,max_value=5000,value=item['fade_ms'],key=key+'_fade'))
