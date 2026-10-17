# 品質レビューの動作確認用データ

実際のプロジェクトと分けた、空のフォルダを指定してください。

```bash
python scripts/demo_quality_review.py --output /tmp/manga-quality-demo
MANGA_DATA_DIR=/tmp/manga-quality-demo/test_data python -m streamlit run app.py
```

『君が忘れた、最後の約束』の8ページ24コマを想定し、テスト用図形を保存・採用した作品を作成します。
漫画の原稿・ユーザーデータ・画像生成API・実際のChatGPT返答は使用しません。
既存ファイルのある出力先は拒否します。既存プロジェクトの保存先を指定しないでください。

生成物：

- `test_data/`：専用プロジェクト。ストーリー、2人の人物ID、作画指示、採用画像、参照画像、ページ編集、品質レビューを保存。
- `image_review_materials.zip`：1ページ目と3コマの比較画像、選択した参照画像、プロンプト、添付明細。
- `sample_ai_response.json`：生成した依頼ID・脚本版に対応した模擬AI指摘。最初から取り込み済みで「未確認の提案」として表示。
- `manga.pdf` / `manga_pages.zip` / `manga_vertical.zip`：8ページのテスト出力。
- `validation.json`：描画エラー・検出件数・処理時間。

「完成・書き出し → 品質チェック・修正管理」で、検査結果、縮小一覧、目視回答、AI提案からのタスク登録を試せます。
手動レビューと最終確認は未確認のままです。図形を作っただけで人物の顔や人体を合格にしません。

JSONの固定テンプレートは、アプリの「回答用JSON形式をダウンロード」から取得できます。
依頼ID・ストーリー版・対象をそのまま使ってください。別作品や古い依頼のJSONは取り込めません。
