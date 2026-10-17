"""合成後の縮小プレビューと、完成サイズでの同一結果を再利用する。"""
import time
from manga.ui_runtime import ui as st, record_screen
from manga.japanese_text import find_japanese_font
from manga.page_editor import config_hash
from manga.page_render import RenderCache, render_page
from manga.studio_assets import production_fingerprint


def preview(page,latest,artwork,data,images,config):
    font=find_japanese_font(data["settings"]["font_path"])
    fingerprint=(tuple(production_fingerprint(latest,artwork,data,images.root)),config_hash(config),font.signature,2)
    entries=st.session_state.setdefault("_visual_previews",{})
    if fingerprint in entries:return entries[fingerprint]
    draft_data=dict(data,pages=dict(data["pages"],**{str(page["page"]):config}))
    cache=st.session_state.setdefault("composition_render_cache",RenderCache())
    started=time.perf_counter()
    with st.spinner("編集プレビューを更新しています"):
        rendered=render_page(page,latest["id"],artwork,draft_data,images,font,cache,width=min(600,data["settings"]["width"]))
    record_screen("編集プレビュー",time.perf_counter()-started)
    entries[fingerprint]=rendered
    while len(entries)>6 or sum(len(v.png) for v in entries.values())>8*1024*1024:entries.pop(next(iter(entries)))
    return rendered


def compare(page,latest,artwork,data,images,config,base,key):
    if st.checkbox("変更前後を比較",key=f"{key}_compare"):
        left,right=st.columns(2)
        for column,value,label in ((left,base,"変更前（編集開始時）"),(right,config,"変更後（未保存を含む）")):
            with column:
                rendered=preview(page,latest,artwork,data,images,value)
                st.image(rendered.png,caption=label,use_container_width=True)
