# 今後の処理の追加先

ここは将来の画像の手動取り込み・品質確認・セリフ配置・PillowによるPNG/PDF出力などの追加先です。
OpenAI APIや非公式API、ChatGPTのWeb画面の自動操作は使用しません。

現在の構成は次のとおりです。

- `app.py`：企画作成画面
- `models.py` と `storage.py`：企画データとJSON保存
- `prompts.py`：ユーザーがChatGPTへ手動で渡すプロンプト
- `story.py`：受け渡し用JSONの検証と表記揺れの正規化
- `story_storage.py`：ストーリーのスナップショット保存と復元
- `story_ui.py`：ストーリー制作画面

将来、成果物の保存後に `workflow.artifacts` と `completed_steps` を更新する再開機能を追加できます。
第1段階の `budget` 項目は既存JSONとの互換のため残しますが、現在の手動連携では使いません。
ストーリーの履歴はプロジェクト別フォルダに独立して保存するため、既存の企画JSONはそのまま読み込めます。
