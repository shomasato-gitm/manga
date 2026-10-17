# ページ上の吹き出し編集

Streamlit 1.40.2 の公式 `declare_component(path=...)` でローカルのHTMLを配信します。
JavaScriptはこのリポジトリで実装し、第三者の描画ライブラリ、CDN、npmビルド、画像解析、外部APIには依存しません。利用者のMacにはNode.jsや追加のネイティブライブラリは不要です。
Canvas、Pointer Events、通常のpostMessageを使い、macOS 12のブラウザで利用可能な方式を選んでいます。Mac実機・Safariの動作検証は未実施です。ドラッグを利用できない環境でも、右のスライダーと「従来の数値編集」が使えます。

- `streamlit:render` でPNG、コマ枠・文字枠、選択ID、編集トークンを受け取ります。
- 位置とサイズは対象コマの枠線内側の0〜1。8方向のハンドルを使い、コマ内・最小サイズ内へ制限します。
- Pointer Move中はブラウザ内の青い枠だけを更新します。離すと `streamlit:setComponentValue` で操作を返し、Pythonで本文を再描画します。
- Python側でトークン、対象ID、数値の有限性・範囲を再検証し、下書きへ反映します。保存ボタンまでファイルは更新しません。
- 画像は読み取り専用で、合成・文章の描画はPillowに統一します。Canvasの文字描画とは組版を競合させません。

## 開発用の実ブラウザテスト

通常の自動テストは `python -m pytest -q` です。AppTestが実行しないJavaScriptのドラッグ操作は、別途Playwrightで検証します。
Playwrightは**開発テスト専用**で、アプリの実行依存には追加していません。ドライバはApache-2.0、ブラウザ本体は利用するブラウザのライセンスに従います。
以下はLinux等の開発環境の例です。必ず空の一時保存先を使ってください。

```bash
python -m pip install -r requirements-dev.txt
PYTHONPATH=tests python - <<'PY'
from pathlib import Path
from page_editor_fixtures import create_example
create_example(Path('/tmp/manga-page-browser-fixture'))
PY
MANGA_DATA_DIR=/tmp/manga-page-browser-fixture python -m streamlit run app.py \
  --server.address 127.0.0.1 --server.port 8518 --server.headless true \
  --browser.gatherUsageStats false
```

別のターミナルで実行します。インストール時の通信はテストツール取得用で、アプリやテスト操作からChatGPTへの通信はありません。

```bash
npm install --prefix /tmp/manga-page-browser-tools playwright@1.58.2
node /tmp/manga-page-browser-tools/node_modules/playwright/cli.js install chromium
NODE_PATH=/tmp/manga-page-browser-tools/node_modules \
node tests/browser/page_canvas.cjs http://127.0.0.1:8518 \
  /tmp/manga-page-browser-fixture /tmp/manga-page-browser-result
```

インストール済みChromiumを使う場合は `MANGA_CHROMIUM=/usr/bin/chromium` 等を指定できます。
検証スクリプトは専用マーカーのある未編集のテストプロジェクトだけを受け付け、外部へのブラウザ通信を遮断します。
ドラッグ・サイズ変更の後も保存前のJSONが不変であること、保存後の座標、JavaScriptエラーがないことを検証し、結果JSONとCanvasのスクリーンショットを出力します。
テストが完了したら、起動したサーバーをControl+Cで終了してください。
