"""長文より先にプロンプト取得操作を表示する。コピーは利用者のクリック時だけ。"""
import base64
import streamlit.components.v1 as components
from manga.ui_runtime import ui as st


def prompt_tools(prompt, key, filename="prompt.txt", request=None, *, response_json=True, next_step=None):
    st.subheader("次の操作：プロンプトを取得してChatGPTへ手動送信")
    if request:
        targets=request["targets"]
        target_text="全体" if not targets else "、".join(f"{t['page']}ページ・{t['panel']}コマ" for t in targets[:6]) + (f" ほか（全{len(targets)}コマ）" if len(targets)>6 else "")
        st.caption(f"依頼ID：{request['id']} ｜ 対象：{target_text} ｜ ストーリー版：{request['story_version_id']}")
    st.download_button("依頼プロンプトをダウンロード",prompt,file_name=filename,mime="text/plain",key=f"{key}_download")
    encoded=base64.b64encode(prompt.encode()).decode()
    components.html('''<button id="copy" style="padding:8px 18px;cursor:pointer">プロンプトをコピー</button> <span id="result"></span>
<script>const t=new TextDecoder().decode(Uint8Array.from(atob("'''+encoded+'''"), c=>c.charCodeAt(0)));
document.getElementById('copy').onclick=async()=>{try{await navigator.clipboard.writeText(t);document.getElementById('result').textContent='コピーしました';}catch(e){document.getElementById('result').textContent='コピーを利用できません。ダウンロードを使ってください。';}};</script>''',height=55)
    if st.checkbox("詳細を表示（プロンプト全文）",key=f"{key}_details"):
        st.code(prompt,language="text",wrap_lines=True)
    if response_json:
        st.info("ChatGPTの返答をJSONファイルとして保存し、続く取り込み欄から選択してください。貼り付けも選べます。")
    else:
        st.info(next_step or "基準画像を必要に応じて添付し、ChatGPTで画像を生成してください。生成画像を保存して、下の画像登録欄から戻します。")
