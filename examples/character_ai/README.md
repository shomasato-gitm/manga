# 人物モック・場面例外の確認例

`scene_exception.json`はバイブルへ登録する対象コマ付き例外の形式例です。ファイルを置いただけで自動採用しません。
画面では人物、対象コマ、変更項目、設定値、理由を選び、バイブルの確認操作で新しい版として保存できます。

完全な人物提案・バイブル・検査JSONは専用fixtureから再作成します。

```bash
python scripts/verify_character_ai.py --output /tmp/manga-character-new-demo
```

未作成の出力先だけを受け付け、sample.json / report.json / unified_panel_prompt.txtと独立した作品データを生成します。
実ユーザーデータ・APIは使いません。報告には既存人物UUID・基準画像の保持、前後のSHA、未解決の静的指摘も含めます。
画像はPillowで作る小さな色面素材で、人物の外見・画像生成品質の試験ではありません。

[操作ガイド](../../docs/CHARACTER_AI_STUDIO.md)を参照してください。
