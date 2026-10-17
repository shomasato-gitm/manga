"""ページ上で選択・ドラッグする編集UI。すべて下書きに反映し、保存だけを明示実行する。"""
import copy
import json
from uuid import uuid4
from manga.ui_runtime import ui as st, operation
from manga.page_layout import validate_page, pixel_frames, TEMPLATES
from manga.page_editor import new_draft, config_hash, event_token, canvas_event, replace_bubbles
from manga.page_editor_controls import bubble_controls, image_controls
from manga.page_editor_preview import preview, compare
from manga.page_canvas_ui import page_canvas
from manga.bubble_layout import proposed_bubbles, size_for_text
from manga.japanese_text import TextLayoutError
from manga.storage import StorageError


def _change(draft,config):
    if config != draft["config"]:
        draft.setdefault("undo",[]).append(copy.deepcopy(draft["config"]))
        del draft["undo"][:-30]
        draft["config"]=copy.deepcopy(config)


def _proposal(draft,page,settings,project_id,key,show_preview):
    if st.button("脚本から吹き出しを提案",key=f"{key}_propose"):
        frames=pixel_frames(page,draft["config"],settings)
        bubbles,notes=proposed_bubbles(project_id,page,frames,settings)
        draft["proposal"]=dict(bubbles=bubbles,notes=notes,base=config_hash(draft["config"]))
    proposal_area=st.empty()
    if draft["proposal"]:
        with proposal_area.container():
            proposal=draft["proposal"]
            st.caption("適用前の分割・サイズ案です。既存の吹き出しと保存データはまだ変更していません。")
            st.dataframe([{"コマ":b["panel"],"種類":b["kind"],"話者":b["speaker"],"本文":b["text"],"幅（%）":round(b["box"]["width"]*100,1),"高さ（%）":round(b["box"]["height"]*100,1),"要確認":b["needs_review"]} for b in proposal["bubbles"]],hide_index=True,use_container_width=True)
            for note in proposal["notes"]:st.warning(note)
            if st.checkbox("分割案をページでプレビュー",key=f"{key}_proposal_preview"):
                show_preview(proposal["bubbles"])
            agreed=st.checkbox("既存吹き出しを置き換える内容を確認しました",key=f"{key}_proposal_confirm")
            if st.button("分割・サイズ調整案を下書きへ適用",disabled=not agreed,key=f"{key}_apply"):
                if proposal["base"] != config_hash(draft["config"]):
                    st.error("提案後に編集内容が変わりました。もう一度提案を作成して確認してください。")
                else:
                    _change(draft,replace_bubbles(draft["config"],proposal["bubbles"]))
                    draft.update(proposal=None,epoch=str(uuid4()),selected=None)
                    st.rerun()


def _history(store,data,page,latest,notice,key):
    events={i:e for i,e in enumerate(data["history"]) if e["kind"]=="page" and e["page"]==page["page"] and e["before"] is not None}
    with st.expander("保存した編集履歴から復元"):
        if not events:st.caption("復元できる変更前のページはまだありません。");return
        index=st.selectbox("復元するページ編集履歴",list(reversed(events)),format_func=lambda i:events[i]["saved_at"]+f" / 保存{ i+1 }直前",key=f"{key}_history")
        if st.checkbox("復元する配置・文章を確認",key=f"{key}_history_show"):st.json(events[index]["before"])
        st.caption("復元は保存を伴います。このページの未保存下書きを終了し、復元前の保存済みページも履歴に残します。")
        confirmed=st.checkbox("履歴を現在のページへ復元する",key=f"{key}_restore_confirm")
        if st.button("この編集履歴を復元",disabled=not confirmed,key=f"{key}_restore"):
            try:
                with operation("ページ編集履歴を復元しています",invalidate_actions=True):
                    store.restore_page(page["page"],index,story_version_id=latest["id"],expected_revision=data["revision"])
                st.session_state[notice]="編集履歴を復元しました。復元前のページも履歴に保持しています。"
                st.session_state.setdefault("_page_editor_drafts",{}).pop((store.project_id,page["page"],latest["id"]),None)
                st.rerun()
            except (ValueError,StorageError) as error:st.error(str(error))


def visual_editor(project,store,data,page,latest,artwork,images,config,prefix,notice):
    drafts=st.session_state.setdefault("_page_editor_drafts",{})
    cursor=(project.id,page["page"],latest["id"])
    if cursor not in drafts:drafts[cursor]=new_draft(config,data["revision"],latest["id"])
    draft=drafts[cursor]
    dirty=draft["config"] != draft["base"]
    if data["revision"] != draft["revision"] and not dirty:
        drafts[cursor]=draft=new_draft(config,data["revision"],latest["id"])
    for old_cursor,old in drafts.items():
        if old_cursor[:2]==cursor[:2] and old_cursor[2]!=cursor[2] and old["config"]!=old["base"]:
            st.warning("古いストーリー版の未保存ページ編集を保持しています。新しい版へは自動適用しません。")
            st.download_button("古い版のページ下書きを取得",json.dumps(old["config"],ensure_ascii=False,indent=2),file_name=f"page_{page['page']}_draft.json",key=f"{prefix}_{old_cursor[2]}_old_draft")
    key=f"{prefix}_visual_{page['page']}_{draft['epoch']}"
    actions=st.empty()
    left,right=st.columns([1.3,1])
    with right:
        with st.empty().container():
            choices=[f"image:{p['panel']}" for p in page["panels"]]+[b["id"] for b in draft["config"]["bubbles"]]
            by_id={b["id"]:b for b in draft["config"]["bubbles"]}
            if draft["selected"] not in choices:draft["selected"]=choices[0]
            def label(value):
                if value.startswith("image:"):return f"{value.split(':')[1]}コマ・画像配置"
                b=by_id[value]
                return f"{b['panel']}コマ・{b.get('speaker') or ('ナレーション' if b['source']=='narration' else 'セリフ')} / {str(b.get('text') or '')[:12]}"
            selected=st.selectbox("編集する対象",choices,index=choices.index(draft["selected"]),format_func=label,key=f"{key}_target")
            draft["selected"]=selected
            changed=copy.deepcopy(draft["config"])
            if selected.startswith("image:"):
                changed=image_controls(changed,int(selected.split(":")[1]),f"{key}_{selected}")
            else:
                b=bubble_controls(by_id[selected],page,f"{key}_{selected}")
                changed["bubbles"]=[b if item["id"]==selected else item for item in changed["bubbles"]]
                if st.button("この枠を文字量に合わせる",key=f"{key}_resize_text"):
                    frame=pixel_frames(page,changed,data["settings"])[b["panel"]-1]
                    text=b["text"] if b["text"] is not None else page["panels"][b["panel"]-1][b["source"]]
                    w,h,review=size_for_text(text,b["flow"],b["shape"],b["tail"],b["font_scale"],frame,data["settings"]["width"])
                    b["box"].update(width=w,height=h,x=min(b["box"]["x"],1-w),y=min(b["box"]["y"],1-h))
                    _change(draft,changed);draft["epoch"]=str(uuid4());st.rerun()
            _change(draft,changed)
            template=st.selectbox("ページのレイアウト",list(TEMPLATES),index=list(TEMPLATES).index(draft["config"]["template"]),format_func=TEMPLATES.get,key=f"{key}_layout")
            if template != draft["config"]["template"]:_change(draft,dict(draft["config"],template=template,frames=None))
            with st.expander("文字枠を手動で追加"):
                target=st.selectbox("新しい文字枠のコマ",[p["panel"] for p in page["panels"]],key=f"{key}_add_panel")
                narration=st.checkbox("ナレーション枠として追加",key=f"{key}_add_narration")
                if st.button("新しい文字枠を下書きに追加",key=f"{key}_add"):
                    panel=copy.deepcopy(page["panels"][target-1]);panel.update(dialogue="" if narration else "セリフを入力",narration="ナレーションを入力" if narration else "")
                    bubbles,_=proposed_bubbles(project.id,dict(page,panels=[panel]),[pixel_frames(page,draft["config"],data["settings"])[target-1]],data["settings"])
                    b=bubbles[0];b.update(id=str(uuid4()),text=b["text"],source="custom",needs_review=False)
                    _change(draft,dict(draft["config"],bubbles=draft["config"]["bubbles"]+[b]));draft.update(selected=b["id"],epoch=str(uuid4()));st.rerun()
    valid=True
    try:validate_page(draft["config"])
    except (ValueError,KeyError,TypeError) as error:valid=False;st.error(f"配置を確認してください：{error}")
    with actions.container():
        dirty=draft["config"] != draft["base"] or str(page["page"]) not in data["pages"]
        st.caption("未保存のページ編集があります。" if dirty else "保存済みの内容です。")
        if data["revision"] != draft["revision"]:
            st.warning("他の保存でページ編集が更新されました。現在の下書きを取得し、キャンセルして最新版を読み直してください。上書きは防止します。")
            st.download_button("現在のページ下書きを取得",json.dumps(draft["config"],ensure_ascii=False,indent=2),file_name=f"page_{page['page']}_draft.json",key=f"{key}_draft_download")
        save_col,cancel_col,undo_col=st.columns(3)
        if save_col.button("ページの編集を保存",type="primary",disabled=not valid or not dirty,key=f"{key}_save"):
            try:
                updated=dict(draft["config"],source_page=copy.deepcopy(page),source_version_id=latest["id"])
                with operation("ページの配置・文章を保存しています",invalidate_actions=True):
                    store.save_page(page["page"],updated,story_version_id=latest["id"],expected_revision=draft["revision"])
                drafts[cursor]=new_draft(updated,draft["revision"]+1,latest["id"])
                drafts[cursor]["selected"]=draft["selected"]
                st.session_state[notice]="ページ編集を保存しました。完成プレビュー・出力に同じ配置を反映します。";st.rerun()
            except (ValueError,StorageError) as error:st.error(str(error)+" 下書きは保持しています。")
        if cancel_col.button("キャンセル（保存済みに戻す）",key=f"{key}_cancel"):
            drafts[cursor]=new_draft(config,data["revision"],latest["id"])
            if draft["selected"] in [b["id"] for b in config["bubbles"]] or str(draft["selected"]).startswith("image:"):
                drafts[cursor]["selected"]=draft["selected"]
            st.rerun()
        if undo_col.button("下書きを1つ戻す",disabled=not draft.get("undo"),key=f"{key}_undo"):
            draft["config"]=draft["undo"].pop();draft.update(epoch=str(uuid4()),proposal=None);st.rerun()
    with left:
        with st.empty().container():
            st.caption("文字枠：クリックして選択・ドラッグ。画像：右のスライダー。")
            st.caption("顔検出は行いません。初期配置と人物の重なりを確認してください。")
            if valid:
                try:
                    rendered=preview(page,latest,artwork,data,images,draft["config"])
                    if not rendered.errors:draft["last_valid"]=copy.deepcopy(draft["config"])
                    token=event_token(draft)
                    event=page_canvas(rendered.png,page,draft["config"],data["settings"],token,selected,f"{prefix}_{page['page']}_{latest['id']}_canvas")
                    if isinstance(event,dict) and event.get("token")==token and event.get("event_id") != draft.get("last_event"):
                        updated,bubble_id=canvas_event(draft["config"],event,token)
                        _change(draft,updated);draft.update(selected=bubble_id,last_event=event["event_id"],epoch=str(uuid4()));st.rerun()
                    for error in rendered.errors:st.error(error)
                    for warning in rendered.warnings:st.warning(warning)
                    for b in draft["config"]["bubbles"]:
                        if b.get("needs_review"):st.warning(f"{b['panel']}コマ：分割・話者が未確認です。右の編集欄で原文と確認してください。")
                    compare(page,latest,artwork,data,images,draft["config"],draft["base"],key)
                except (ValueError,TextLayoutError,StorageError,OSError) as error:st.error(str(error))
            elif draft.get("last_valid"):
                try:
                    last=preview(page,latest,artwork,data,images,draft["last_valid"])
                    st.image(last.png,caption="最後の有効なプレビュー。枠外の入力は未反映です。右側で位置・サイズを調整してください。",use_container_width=True)
                except (ValueError,TextLayoutError,StorageError,OSError) as error:st.error(str(error))
    with st.expander("脚本から話者別の分割・サイズ調整をやり直す"):
        def show_preview(bubbles):
            try:
                result=preview(page,latest,artwork,data,images,replace_bubbles(draft["config"],bubbles))
                st.image(result.png,caption="適用前の分割・サイズ案",use_container_width=True)
                for error in result.errors:st.error(error)
            except (ValueError,TextLayoutError,StorageError,OSError) as error:st.error(str(error))
        _proposal(draft,page,data["settings"],project.id,key,show_preview)
    _history(store,data,page,latest,notice,key)
