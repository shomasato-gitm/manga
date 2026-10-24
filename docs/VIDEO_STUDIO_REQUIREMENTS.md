# 音声・動画スタジオ要件

## Phase 3の範囲

既存漫画の保存済みストーリー・人物UUID・ナレーターから発話を抽出し、人物ごとの声と発話ごとの読み・感情・速度を管理します。
6工程を維持し、任意の「完成・書き出し → 音声制作・字幕」だけで準備・生成・試聴・履歴・字幕出力を行います。
非表示の音声画面ではモデル・音声ファイルを読みません。試聴は選択した音声だけ、履歴一覧は直近30件を表示します。

Pollyの低価格・日本語標準音声を実装先にし、モックを既定とします。公式比較を [別紙](tts-provider-comparison.md) に保存します。
有料送信は既存BudgetedExecutorだけを通し、明示承認・確定脚本・確認済み料金・費用枠が欠けたら停止します。
開発・CIでは実課金APIを禁止します。OpenAI API・非公式API・ChatGPTの自動操作は追加しません。

## 構成

| モジュール | 責任 |
| --- | --- |
| voice_models / voice_script | 独立形式・声設定・キャッシュキー、既存split_dialogueの再利用、曖昧な話者の確認 |
| voice_storage / voice_service / voice_jobs | 保存競合、元データ照合、生成計画、ジョブ・履歴・復旧、予算ゲート |
| voice_pricing / voice_polly | 手動確認の料金、上限算定、SigV4、SSML、応答の利用量、秘匿化 |
| voice_audio | PCM/WAV、実尺・整合検査、任意のffprobe、無料確認用トーン |
| voice_timeline / voice_contract | 独立JSON・SRTと次工程の検証契約 |
| voice_uiと3つのサブビュー | 選択中の作業だけ描画、料金・処理中・失敗・復旧の案内 |

標準ライブラリと既存依存だけを使い、新しい音声SDKや重いMLパッケージを必須にしません。
Python 3.11、Streamlit 1.40.2、Intel Mac/macOS 12を想定します。実機・実音声の確認は別途必要です。
既存のstory/schema_version:1・artwork・review・composition・GitHub同期の形式は変更しません。
音声はvoice.jsonとvoice/へ分離し、候補・元画像・以前の音声・予算実績を自動削除しません。
新しい音声バイナリは既存のGitHub作品同期へ自動追加しません。

## Phase 4への引き渡し

[VOICE_TIMELINE.md](VOICE_TIMELINE.md) の実WAVと実尺・字幕・採用画像参照を使用します。
Phase 3自体にはMP4を追加しません。Phase 4の別モジュールが [VIDEO_RENDERING.md](VIDEO_RENDERING.md) の3形式MP4を生成します。
動画計画・履歴・素材コピーはvideo.jsonとvideo/へ分離します。SNS投稿・無許諾音声クローン・BGM/SFXの自動取得は実装しません。
要件・費用・異常系の確認は [費用方針](VIDEO_COST_POLICY.md)、[テスト方針](VIDEO_TEST_PLAN.md)、[操作手順](VOICE_PRODUCTION.md) を参照してください。
