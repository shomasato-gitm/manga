"""選択した画像・文字枠の編集。スライダーと従来の数値編集を使い分ける。"""
import copy
from manga.ui_runtime import ui as st
from manga.page_layout import bubble_text
from manga.page_images import MODES, placement_for


def _set(changed,original,key,value,default):
    if key in original and isinstance(value,(float,int)) and not isinstance(value,bool) and abs(value-original[key]) < .000001:
        value=original[key]
    if key == "background" and isinstance(original.get(key),str) and value.lower()==original[key].lower():value=original[key]
    if key == "opacity" and key in original and abs(value-original[key])<=.005001:value=original[key]
    if key in original or value != default:changed[key]=value


def bubble_controls(bubble,page,key):
    changed=copy.deepcopy(bubble)
    actual=bubble_text(bubble,page["panels"][bubble["panel"]-1])
    content=st.text_area("本文（ページ専用）",value=actual,max_chars=4000,key=f"{key}_text")
    if content != actual:changed.update(text=content,text_layout="japanese_v2")
    speaker=st.text_input("話者（画像には表示しません）",value=bubble.get("speaker",""),max_chars=200,key=f"{key}_speaker")
    _set(changed,bubble,"speaker",speaker,"")
    kind=st.selectbox("文字枠の種類",["speech","narration"],index=1 if bubble.get("kind", "narration" if bubble["source"]=="narration" else "speech")=="narration" else 0,format_func=lambda v:{"speech":"セリフ","narration":"ナレーション"}[v],key=f"{key}_kind")
    _set(changed,bubble,"kind",kind,"narration" if bubble["source"]=="narration" else "speech")
    flow=st.selectbox("文字方向",["vertical","horizontal"],index=0 if bubble["flow"]=="vertical" else 1,format_func=lambda v:"縦書き" if v=="vertical" else "横書き",key=f"{key}_flow")
    changed["flow"]=flow
    if flow != bubble["flow"]:changed["text_layout"]="japanese_v2"
    narration=kind=="narration" and ("kind" in bubble or kind != ("narration" if bubble["source"]=="narration" else "speech"))
    shape=st.selectbox("枠の形",["rectangle"] if narration else ["ellipse","rectangle"],index=0 if narration or bubble["shape"]=="ellipse" else 1,format_func=lambda v:"楕円" if v=="ellipse" else "四角",key=f"{key}_{kind}_shape")
    changed["shape"]=shape
    changed["tail"]=False if narration else st.checkbox("しっぽを表示",value=bubble["tail"],key=f"{key}_tail")
    changed["visible"]=st.checkbox("文字枠を表示",value=bubble["visible"],key=f"{key}_visible")
    for field,label in (("x","横位置"),("y","縦位置"),("width","横幅"),("height","縦幅")):
        value=st.slider(f"{label}（%）",10.0 if field in ("width","height") else 0.0,100.0,float(round(bubble["box"][field]*100,3)),step=.5,key=f"{key}_{field}")/100
        changed["box"][field]=bubble["box"][field] if abs(value-bubble["box"][field]) < .00001 else value
    with st.expander("文字サイズ・背景の詳細"):
        changed["font_scale"]=st.number_input("文字サイズ（ページ幅の%）",min_value=1.2,max_value=7.0,value=round(bubble["font_scale"]*100,3),step=.1,key=f"{key}_font")/100
        if abs(changed["font_scale"]-bubble["font_scale"]) < .000001:changed["font_scale"]=bubble["font_scale"]
        modern=st.checkbox("可読サイズの下限を適用",value="min_font_scale" in bubble,key=f"{key}_modern")
        minimum=st.number_input("最小文字サイズ（ページ幅の%）",min_value=1.2,max_value=7.0,value=round(bubble.get("min_font_scale",.02)*100,3),step=.1,key=f"{key}_min_font")/100
        if modern:_set(changed,bubble,"min_font_scale",minimum,None)
        else:changed.pop("min_font_scale",None)
        if "min_font_scale" not in bubble:st.caption("旧枠の自動縮小条件を維持しています。下限を有効にすると、小さすぎる文字の代わりに警告を表示します。")
        background=st.color_picker("枠の背景色",value=bubble.get("background","#ffffff"),key=f"{key}_background")
        _set(changed,bubble,"background",background,"#ffffff")
        opacity=st.slider("背景の不透明度（%）",0,100,round(bubble.get("opacity",1)*100),key=f"{key}_opacity")/100
        _set(changed,bubble,"opacity",opacity,1)
        outline=st.checkbox("枠線を表示",value=bubble.get("outline",True),key=f"{key}_outline")
        _set(changed,bubble,"outline",outline,True)
    if bubble.get("needs_review"):st.warning("分割・話者が未確認です。原文を確認し、必要なら本文・話者を修正してください。")
    with st.expander("分割前の原文を確認"):
        st.text(bubble.get("source_text",actual))
    if changed["font_scale"] < .018:st.warning("文字サイズが小さめです。完成サイズで可読性を確認してください。")
    return changed


def image_controls(config,panel,key):
    placement=placement_for(config,panel)
    mode=st.selectbox("画像の表示方式",list(MODES),index=list(MODES).index(placement["mode"]),format_func=MODES.get,key=f"{key}_mode")
    x=st.slider("画像の横位置（左端 → 右端）",0.0,100.0,float(placement["x"]*100),step=1.0,key=f"{key}_x")/100
    y=st.slider("画像の縦位置（上端 → 下端）",0.0,100.0,float(placement["y"]*100),step=1.0,key=f"{key}_y")/100
    zoom=st.slider("画像のズーム倍率",1.0,4.0,float(placement["zoom"]),step=.05,disabled=mode=="contain",key=f"{key}_zoom")
    st.caption("全体表示では倍率を適用せず、画像全体を保持します。トリミングは配置情報だけを保存し、元画像を変更しません。")
    changed=dict(config)
    value=dict(placement,mode=mode,x=x,y=y,zoom=zoom)
    for field in ("x","y","zoom"):
        if abs(value[field]-placement[field])<.0000001:value[field]=placement[field]
    if value != placement:
        changed["image_placements"]=dict(config.get("image_placements",{}),**{str(panel):value})
    return changed
