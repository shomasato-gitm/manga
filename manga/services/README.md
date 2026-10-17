# 今後の処理の追加先

ここは将来の品質確認や制作工程管理などの追加先です。ページ編集・出力は以下の専用モジュールに実装しています。
OpenAI APIや非公式API、ChatGPTのWeb画面の自動操作は使用しません。

現在の構成は次のとおりです。

- `app.py`：企画作成画面
- `models.py` と `storage.py`：企画データとJSON保存
- `prompts.py`：ユーザーがChatGPTへ手動で渡すプロンプト
- `story.py`：受け渡し用JSONの検証と表記揺れの正規化
- `story_storage.py`：ストーリーのスナップショット保存と復元
- `story_ui.py`：ストーリー制作画面
- `editorial_models.py` と `editorial_storage.py`：参考作品・分析・評価・確定の検証と保存
- `editorial_prompts.py` と `reference_ui.py`：参考分析・評価プロンプトと参考作品の画面
- `editorial_ui.py` と `editorial_ui_common.py`：評価・改善・比較・確定と共通JSON入力
- `editorial_workflow.py`：保存記録から制作状況を計算
- `story_comparison.py`：JSON値に基づく差分と部分改善の範囲チェック
- `artwork_models.py` と `artwork_storage.py`：作画設定・参照画像・候補画像のローカル保存
- `images.py`：静止画の検証・向きの補正・PNG化
- `image_prompts.py`：キャラクターとコマの画像生成用プロンプト（ユーザーが手動でChatGPTへ渡す）
- `artwork_ui.py`：キャラクター設定・コマの作画指示・画像管理画面

将来、成果物の保存後に `workflow.artifacts` と `completed_steps` を更新する再開機能を追加できます。
第1段階の `budget` 項目は既存JSONとの互換のため残しますが、現在の手動連携では使いません。
ストーリーの履歴はプロジェクト別フォルダに独立して保存するため、既存の企画JSONはそのまま読み込めます。
作画設定も独立した `artwork.json` に保存します。画像そのものは `images/` に保存し、JSONには相対パスを記録します。
キャラクター削除は一覧からの非表示で、画像候補や登録時点の人物情報は保持します。
参考作品と分析・評価は `editorial.json` に分離し、評価はUUIDで対象のストーリー版を指します。
改善版は既存の検証処理で保存し、`story_history.json` の版メタデータに改善元・評価ID・理由を記録します。
確定後の変更通知は保存した脚本と現在のJSON内容の実際の比較に基づき、画像・作画指示を自動変更しません。

- `page_layout.py` と `page_storage.py`：ページ配置・吹き出しの検証と独立JSONへの履歴保存
- `japanese_text.py`：日本語フォント検証と縦横書きの文字配置
- `page_render.py`：元画像を変更しない合成とコマ単位キャッシュ
- `page_exports.py`：PNG・全ページPDF・縦読みZIPの生成
- `page_ui.py`：ページ編集・プレビュー・ダウンロード画面

第5段階の制作支援は `assistant_*.py` に分割しています。
依頼・返答のJSON検証、保存・明示的な選択採用、プロンプト、サンプル、一覧・編集・作画評価の画面を分離しています。
提案は `assistant.json` へ保存し、採用対象だけを既存の作画設定・参考作品へ反映します。
複数JSONの採用保存は `assistant_pending.json` に変更前後を先に記録し、途中失敗後は明示操作で完了できます。
従来の人物ID・参照画像・候補画像は保持し、画像生成・ChatGPTへの送信は自動化しません。

第6段階では `navigation.py` から選択した画面だけを呼びます。表示用データは `ui_data.py`、セッションの下書きと処理状態は `ui_runtime.py` へ分離しています。
工程判定は `studio_workflow.py`、描画・出力の成功記録は独立した `studio.json`、容量計測は `storage_usage.py` を使います。
保存処理のロック・競合検知・検証はキャッシュせず従来どおり実行し、画像や既存の保存履歴は変更・削除しません。
