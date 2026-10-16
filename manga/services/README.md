# 今後の処理の追加先

ここは将来の品質確認・セリフ配置・PillowによるPNG/PDF出力などの追加先です。
OpenAI APIや非公式API、ChatGPTのWeb画面の自動操作は使用しません。

現在の構成は次のとおりです。

- `app.py`：企画作成画面
- `models.py` と `storage.py`：企画データとJSON保存
- `prompts.py`：ユーザーがChatGPTへ手動で渡すプロンプト
- `story.py`：受け渡し用JSONの検証と表記揺れの正規化
- `story_storage.py`：ストーリーのスナップショット保存と復元
- `story_ui.py`：ストーリー制作画面
- `artwork_models.py` と `artwork_storage.py`：作画設定・参照画像・候補画像のローカル保存
- `images.py`：静止画の検証・向きの補正・PNG化
- `image_prompts.py`：キャラクターとコマの画像生成用プロンプト（ユーザーが手動でChatGPTへ渡す）
- `artwork_ui.py`：キャラクター設定・コマの作画指示・画像管理画面

将来、成果物の保存後に `workflow.artifacts` と `completed_steps` を更新する再開機能を追加できます。
第1段階の `budget` 項目は既存JSONとの互換のため残しますが、現在の手動連携では使いません。
ストーリーの履歴はプロジェクト別フォルダに独立して保存するため、既存の企画JSONはそのまま読み込めます。
作画設定も独立した `artwork.json` に保存します。画像そのものは `images/` に保存し、JSONには相対パスを記録します。
キャラクター削除は一覧からの非表示で、画像候補や登録時点の人物情報は保持します。
