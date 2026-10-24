# APIなしの企画・脚本サンプル

すべて確認用モックの出力です。実モデルの創作品質・採点精度・請求を検証したものではありません。

- concepts_response.json：5案を返すJSON例。案IDはアプリが割り当てます。
- story_response.json：初稿のstory/schema_version:1と、別メタデータのpage_hooks・change_reason。
- improved_response.json：5回改善後の同形式。
- evaluation_response.json：8項目評価。story_version_idは生成した専用fixtureの候補IDです。別作品へそのまま適用できません。
- pricing_20261024.json：公式確認日の料金設定例。確認フラグはfalseで配布し、実行に使う前に公式条件を確認します。為替・税は仮定です。

アプリはこれらを自動採用しません。無料モックを選ぶと、同じ検証・保存経路で自分の作品IDに対応する候補を生成できます。
CLIでは次を実行します。既存ディレクトリは上書きしません。

```bash
python scripts/verify_story_ai.py --output /tmp/manga-story-ai-new-demo
```

作成したsample.jsonには5案・初稿・改善版・ページの引き・評価、report.jsonには反復回数と採用先ID等を保存します。
仕様・費用・操作は [制作ガイド](../../docs/STORY_AI_STUDIO.md) を参照してください。
