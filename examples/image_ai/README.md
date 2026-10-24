# 画像制作のオフライン確認資料

`provider_research_20261024.json`は公式再取得日付きの比較資料です。モデル名・単価はこのJSONで管理しますが、実行用料金カードではありません。
全候補のmaximum_charge_verified・execution_availableはfalseで、有料送信を有効化しません。Vertexの参考情報とGemini Developer APIの未確認情報を分けています。

APIなしの候補・計画・採用・音声／動画素材の例は、新規専用フォルダで再作成します。

```bash
python scripts/verify_image_ai.py --output /tmp/manga-image-ai-new-demo
```

3人物＋ナレーター、8ページ24コマ、29候補（モック28・手動1）、基準画像3件とコマ画像24件の明示採用を検証します。
sample.json、report.json、timeline.jsonと独立作品を保存します。画像と音声は明らかなテスト素材で、完成品質の例ではありません。
既存出力先は上書きしません。[操作ガイド](../../docs/IMAGE_AI_STUDIO.md)を参照してください。
