"""採用済みコマのページ編集。画像生成・外部送信は行わない。"""

import copy
from uuid import uuid4

from manga.ui_runtime import ui as st, operation
from manga.ui_data import read_store

from manga.artwork_storage import ArtworkStore
from manga.japanese_text import TextLayoutError, find_japanese_font
from manga.page_exports import export_book, export_vertical, page_png
from manga.page_layout import ASPECTS, TEMPLATES, WIDTHS, bubble_text, default_bubble, effective_page, template_frames
from manga.page_render import RenderCache, render_page
from manga.page_storage import PageStore
from manga.storage import StorageError


SOURCES = {"dialogue": "セリフ", "narration": "ナレーション", "custom": "自由文"}


def _save(action, notice):
    try:
        with operation("ページ編集を保存しています", invalidate_actions=True):
            action()
        st.session_state[notice] = "ページ編集を保存しました。"
        st.rerun()
    except (ValueError, StorageError) as error:
        st.error(str(error))


def _coordinates(box, prefix, label):
    columns = st.columns(4)
    result = {}
    for column, key, name in zip(columns, ("x", "y", "width", "height"), ("左から", "上から", "幅", "高さ")):
        result[key] = column.number_input(f"{label}：{name}（%）", min_value=0.0, max_value=100.0,
                                         value=float(round(box[key] * 100, 4)), step=1.0, format="%.2f", key=f"{prefix}_{key}") / 100
    return result


def _settings(store, data, prefix, notice):
    current = data["settings"]
    with st.expander("出力サイズ・余白・日本語フォント"):
        with st.form(f"{prefix}_settings_{data['revision']}"):
            settings = dict(current)
            settings["width"] = st.selectbox("ページの横幅（px）", WIDTHS, index=WIDTHS.index(current["width"]))
            settings["aspect"] = st.selectbox("ページの縦横比", list(ASPECTS), index=list(ASPECTS).index(current["aspect"]), format_func=lambda key: ASPECTS[key][0])
            for key, label, low, high in (("margin", "ページ余白", 1.0, 10.0), ("gutter", "コマ間隔", 0.0, 5.0), ("border", "枠線の太さ", 0.0, 1.0)):
                settings[key] = st.number_input(f"{label}（ページ幅の%）", min_value=low, max_value=high, value=float(current[key]), step=0.1)
            settings["dpi"] = st.selectbox("出力DPI", (72, 150, 300), index=(72, 150, 300).index(current["dpi"]))
            settings["font_path"] = st.text_input("日本語フォントのファイルパス（空欄で自動検出）", value=current["font_path"], max_chars=2000)
            st.caption("Macのヒラギノ、Noto CJKなどのTTF・OTF・TTCに対応。未対応文字や収まらない文章はエラーにし、出力を止めます。")
            submitted = st.form_submit_button("ページ共通設定を保存")
        if submitted:
            _save(lambda: store.save_settings(settings, expected_revision=data["revision"]), notice)


def _layout(config, settings, key, save):
    with st.expander("コマ割りを編集", expanded=True):
        template = st.selectbox("レイアウトテンプレート", list(TEMPLATES), index=list(TEMPLATES).index(config["template"]), format_func=lambda name: TEMPLATES[name], key=f"{key}_template")
        manual = st.checkbox("コマの配置を手動調整", value=config["frames"] is not None, key=f"{key}_manual")
        st.caption("コマ番号が読み順です。テンプレートは上→下、同じ段は右→左。手動座標はページ余白の内側を100%とします。")
        frames = config["frames"] if config["frames"] is not None and template == config["template"] else template_frames(template, len(config["source_page"]["panels"]), settings)
        with st.form(f"{key}_layout_{template}_{manual}"):
            if manual:
                frames = [_coordinates(frame, f"{key}_{template}_frame_{index}", f"{index + 1}コマ") for index, frame in enumerate(frames)]
            submitted = st.form_submit_button("このページのコマ割りを保存")
        if submitted:
            save(dict(config, template=template, frames=frames if manual else None))


def _bubbles(config, page, project_id, key, save, settings):
    with st.expander("セリフ・吹き出しを編集"):
        st.caption("セリフ・ナレーションはストーリー本文に追従します。専用の文章に切り替えて分割・調整できます。本文の変更は元のストーリーへ影響しません。")
        by_id = {item["id"]: item for item in config["bubbles"]}
        if by_id:
            selected = st.selectbox("編集する吹き出し", list(by_id), format_func=lambda item: f"{by_id[item]['panel']}コマ・{SOURCES[by_id[item]['source']]}・{list(by_id).index(item) + 1}", key=f"{key}_bubble")
            bubble = by_id[selected]
            bkey = f"{key}_{selected}"
            override = st.checkbox("この吹き出し専用の文章を使う", value=bubble["text"] is not None, disabled=bubble["source"] == "custom", key=f"{bkey}_override")
            with st.form(f"{bkey}_form_{override}"):
                changed = copy.deepcopy(bubble)
                text = st.text_area("吹き出しの本文", value=bubble_text(bubble, page["panels"][bubble["panel"] - 1]), max_chars=4000, disabled=not override)
                changed["text"] = text if override else None
                changed["visible"] = st.checkbox("この吹き出しを表示", value=bubble["visible"])
                changed["box"] = _coordinates(bubble["box"], f"{bkey}_box", "吹き出し")
                changed["shape"] = st.selectbox("吹き出しの形", ("ellipse", "rectangle"), index=("ellipse", "rectangle").index(bubble["shape"]), format_func=lambda value: {"ellipse": "楕円", "rectangle": "四角"}[value])
                changed["flow"] = st.selectbox("文字の方向", ("vertical", "horizontal"), index=("vertical", "horizontal").index(bubble["flow"]), format_func=lambda value: {"vertical": "縦書き（右→左）", "horizontal": "横書き"}[value])
                changed["tail"] = st.checkbox("吹き出しにしっぽを付ける", value=bubble["tail"])
                changed["font_scale"] = st.number_input("文字サイズ上限（ページ幅の%）", min_value=1.2, max_value=7.0, value=round(bubble["font_scale"] * 100, 4), step=0.1) / 100
                submitted = st.form_submit_button("吹き出しを保存")
            if submitted:
                if changed["flow"] != bubble["flow"] and all(abs(changed["box"][field]-bubble["box"][field]) < .00001 for field in ("x","y","width","height")) and bubble.get("text_layout") == "japanese_v2":
                    from manga.bubble_layout import size_for_text
                    from manga.page_layout import pixel_frames
                    content=bubble_text(changed,page["panels"][changed["panel"]-1])
                    frame=pixel_frames(page,config,settings)[changed["panel"]-1]
                    w,h,_=size_for_text(content,changed["flow"],changed["shape"],changed["tail"],changed["font_scale"],frame,settings["width"])
                    changed["box"].update(width=w,height=h,x=min(changed["box"]["x"],1-w),y=min(changed["box"]["y"],1-h))
                updated = copy.deepcopy(config)
                updated["bubbles"] = [changed if item["id"] == selected else item for item in config["bubbles"]]
                save(updated)
        with st.form(f"{key}_add_bubble"):
            panel = st.selectbox("吹き出しを追加するコマ", [item["panel"] for item in page["panels"]])
            source = st.selectbox("追加する文章の種類", list(SOURCES), format_func=lambda value: SOURCES[value])
            add = st.form_submit_button("吹き出しを追加")
        if add:
            updated = copy.deepcopy(config)
            updated["bubbles"].append(default_bubble(project_id, page["page"], panel, source, str(uuid4())))
            save(updated)


def _preview(page, latest, artwork, data, images, prefix):
    cache = st.session_state.setdefault("composition_render_cache", RenderCache())
    preview_key = f"{prefix}_preview"
    high_quality=st.checkbox("完成サイズでプレビュー（高品質）",value=True,key=f"{prefix}_preview_quality")
    force = st.button("ページをプレビュー・再描画", key=f"{prefix}_preview_button")
    if force:
        st.session_state[preview_key] = True
    if st.session_state.get(preview_key):
        try:
            font = find_japanese_font(data["settings"]["font_path"])
            from manga.studio_assets import production_fingerprint
            from manga.studio_storage import StudioStore
            fingerprint=production_fingerprint(latest,artwork,data,images.root)
            preview_cache=st.session_state.setdefault("_page_previews",{})
            cache_key=(prefix,page["page"],high_quality)
            entry=preview_cache.get(cache_key)
            if force or entry is None or entry.get("engine") != 2 or entry["fingerprint"] != fingerprint or entry["font"] != font.signature:
                with operation("ページプレビューを描画しています"):
                    rendered=render_page(page, latest["id"], artwork, data, images, font, cache, width=None if high_quality else min(600, data["settings"]["width"]))
                preview_cache[cache_key]=dict(fingerprint=fingerprint,font=font.signature,rendered=rendered,engine=2)
                while len(preview_cache)>8 or sum(len(v["rendered"].png) for v in preview_cache.values())>8*1024*1024:
                    preview_cache.pop(next(iter(preview_cache)))
                if not rendered.errors:
                    StudioStore(images.root.parent,data["project_id"]).record_render(page["page"],fingerprint)
            else:
                rendered=entry["rendered"]
            st.image(rendered.png, caption=f"{page['page']}ページ（{'完成サイズ' if high_quality else '軽量表示'}：{rendered.width}×{rendered.height}px）", use_container_width=True)
            st.caption(f"日本語フォント：{font.name} ｜ 変更のないコマはキャッシュから再利用します。")
            for error in rendered.errors:
                st.error(error)
            for warning in rendered.warnings:
                st.warning(warning)
        except (TextLayoutError, ValueError, StorageError, OSError) as error:
            st.error(str(error))


def _export(page, latest, artwork, data, images, prefix):
    from manga.studio_assets import production_fingerprint
    from manga.studio_storage import StudioStore
    cache=st.session_state.setdefault("composition_render_cache", RenderCache())
    with st.expander("PNG・PDF・縦読みSNSへ出力", expanded=True):
        kind = st.selectbox("出力形式", ("page", "png_zip", "pdf", "sns"), format_func=lambda value: {"page": "表示中ページのPNG", "png_zip": "全ページのPNG（ZIP）", "pdf": "全ページのPDF", "sns": "縦読みSNS画像（ZIP）"}[value], key=f"{prefix}_kind")
        sns_width = 1080
        pages = latest["story"]["pages"]
        if kind == "sns":
            sns_width = st.selectbox("縦読み画像の横幅（px）", (600, 1080), index=1, key=f"{prefix}_sns_width")
            if st.checkbox("縦読みは表示中のページだけ出力", key=f"{prefix}_sns_current"):
                pages = [page]
        st.caption("保存済みの編集内容を出力します。欠けた画像はプレースホルダーになります。縦読みはコマ番号順で、8000px以内のPNGに分割します。")
        state_fingerprint=production_fingerprint(latest,artwork,data,images.root)
        fingerprint = (state_fingerprint, page["page"], kind, sns_width, len(pages), 2)
        export_key = f"{prefix}_export"
        if st.session_state.get(export_key, {}).get("fingerprint") != fingerprint:
            st.session_state.pop(export_key, None)
        if st.button("出力ファイルを生成", key=f"{prefix}_generate") and export_key not in st.session_state:
            try:
                font = find_japanese_font(data["settings"]["font_path"])
                meter=st.progress(0,text="出力対象を確認しています")
                def advance(done,total):
                    meter.progress(done/total,text=f"描画済み {done}/{total}件")
                with operation("保存済みの画像から出力を生成しています"):
                    if kind == "page":
                        rendered = render_page(page, latest["id"], artwork, data, images, font, cache)
                        content, notes = page_png(rendered), rendered.warnings
                        filename, mime = f"page_{page['page']:03d}.png", "image/png"
                    elif kind == "sns":
                        content, notes = export_vertical(pages, latest["id"], artwork, data, images, font, sns_width, cache, progress=advance)
                        filename, mime = "manga_vertical.zip", "application/zip"
                    else:
                        content, notes = export_book(latest["story"], latest["id"], artwork, data, images, font, kind, cache, progress=advance)
                        filename, mime = ("manga.pdf", "application/pdf") if kind == "pdf" else ("manga_pages.zip", "application/zip")
                output_pages=[page["page"]] if kind=="page" else [p["page"] for p in pages]
                studio=StudioStore(images.root.parent,data["project_id"])
                studio.record_export(dict(fingerprint=state_fingerprint,pages=output_pages,kind=kind,filename=filename))
                meter.empty()
                st.session_state[export_key] = dict(fingerprint=fingerprint, content=content, filename=filename, mime=mime, notes=notes)
            except (TextLayoutError, ValueError, StorageError, OSError) as error:
                st.error(str(error))
        if export_key in st.session_state:
            result = st.session_state[export_key]
            for note in result["notes"]:
                st.warning(note)
            st.download_button("完成ファイルをダウンロード", result["content"], file_name=result["filename"], mime=result["mime"], key=f"{prefix}_download")


def render_page_workspace(project, directory, mode="editor"):
    st.header("漫画ページ編集" if mode=="editor" else "PNG・PDF書き出し")
    prefix = f"composition_{project.id}"
    notice = f"{prefix}_notice"
    notice_slot = st.empty()
    if notice in st.session_state:
        notice_slot.success(st.session_state.pop(notice))
    store, images = PageStore(directory, project.id), ArtworkStore(directory, project.id)
    try:
        data, artwork = read_store(store), read_store(images)
        history = read_store(store.stories)
    except StorageError as error:
        st.error(str(error))
        return
    if not history:
        st.info("ストーリーJSONを保存すると、採用したコマ画像から漫画ページを作れます。")
        return
    latest = history[-1]
    if mode == "export":
        _settings(store, data, prefix, notice)
    if mode == "editor":
        page_column,method_column=st.columns([1,2])
        with page_column:
            number=st.selectbox("合成するページ",[p["page"] for p in latest["story"]["pages"]],key=f"{prefix}_{mode}_page")
        with method_column:
            method=st.radio("編集方法",["ビジュアル編集","従来の数値編集"],horizontal=True,key=f"{prefix}_method")
    else:
        number=st.selectbox("出力するページ",[p["page"] for p in latest["story"]["pages"]],key=f"{prefix}_{mode}_page")
    page = latest["story"]["pages"][number - 1]
    saved = data["pages"].get(str(number))
    config = effective_page(saved, project.id, page, latest["id"], data["settings"])
    if saved and saved["source_page"] != page:
        st.warning("保存後にストーリーが変わっています。コマ数が同じ場合は配置を引き継ぎ、異なる場合は一時的に標準配置を使います。元の編集履歴と画像は保持します。内容を確認して再保存してください。")
    key = f"{prefix}_{number}_{latest['id']}_{data['revision']}"

    def save(updated):
        updated = dict(updated, source_page=copy.deepcopy(page), source_version_id=latest["id"])
        _save(lambda: store.save_page(number, updated, story_version_id=latest["id"], expected_revision=data["revision"]), notice)

    if mode == "editor":
        st.caption("出力サイズ・余白・枠線・フォントは「PNG・PDF書き出し」で設定します。")
        with st.empty().container():
            if method == "ビジュアル編集":
                from manga.page_editor_ui import visual_editor
                visual_editor(project,store,data,page,latest,artwork,images,config,prefix,notice)
            else:
                _layout(config, data["settings"], key, save)
                _bubbles(config, page, project.id, key, save, data["settings"])
        with st.expander("保存後の完成プレビュー（書き出しと同じ配置）"):
            st.caption("保存済みの内容を完成サイズで合成して縮小表示します。書き出しには保存前の下書きを含みません。")
            _preview(page, latest, artwork, data, images, prefix)
    else:
        drafts=st.session_state.get("_page_editor_drafts",{})
        if any(c[0]==project.id and d["config"]!=d["base"] for c,d in drafts.items()):
            st.warning("未保存のページ編集があります。書き出しに含める場合は漫画ページ編集へ戻って保存してください。ここでは保存済みの設定を出力します。")
        _export(page, latest, artwork, data, images, prefix)
