# 次タスクへの依存関係：TTS → FFmpeg

PR #2の予算レビューはmainへ反映済みです（基点361444b）。
今回のPhase 3は `feature/video-voice` で音声・字幕を実装し、レビュー用PRにします。実課金テスト・MP4実装・自動マージは行いません。
以下の旧依存関係のうち、TTSの確定契約は [VOICE_TIMELINE.md](VOICE_TIMELINE.md)、操作は [VOICE_PRODUCTION.md](VOICE_PRODUCTION.md) を優先してください。

## 着手順序と不足資料

| タスク | 人間が判断する開始条件 | 新規ブランチ | 主な依存 |
| --- | --- | --- | --- |
| PR #2：反映済み | 人間によるレビュー・マージ済み | feature/video-budget-core-20261024 | 既存予算基盤と全漫画テスト |
| Phase 3：今回 | PR #2の反映を確認済み。TTSはモックで検証 | feature/video-voice | 最新確定脚本・人物ID・予算ゲート・検証済み料金表 |
| CODEX_03_FFMPEG.md | 予算基盤とTTSのPRがmainにマージ済みであることを再確認 | feature/video-renderer | 採用画像・WAV・実測尺・SRT・確定したタイムライン |

mainへの直接push・自動マージは行いません。後続は更新mainを基点とする別タスク／別PRです。
添付のAGENTS.md、START_HERE.md、CODEX_01〜03と現行コードを参照しています。
以前不足していた以下のファイルは、今回既存設計と整合する仕様として新設しました。

- docs/VIDEO_STUDIO_REQUIREMENTS.md
- docs/VIDEO_COST_POLICY.md
- docs/VIDEO_TEST_PLAN.md
- examples/video_timeline_example.json

以下の元のJSON案は将来の動画要件も含みます。Phase 3の実装済み範囲と未実装範囲は新しい仕様書で区別しています。

## TTSが使う予算API

`manga.budget.config.open_budget(projects_directory)` でUIと同じ永続台帳を開きます。
詳細は [予算基盤](video-budget-phase1.md) の新しい `BudgetedExecutor.run()` 契約に従います。
以前の `quote_jpy` だけの呼び出しは安全性不足のため拒否します。Phase 3は `price_quote` と `usage_limits` を渡します。

1. 接続する1プロバイダーについて、公式モデルの存在、商用利用・声の権利、最大入力・出力と請求量を調べます。
2. 公式料金URL・確認日・provider・model・入力/出力/件数単位・税の範囲を `PriceRates` に登録します。最大30日の有効期限を検査します。
3. 為替・税・最大利用量・自動再試行0回から最大JPYを算出し、利用者へ事前表示します。不明な費目があれば有料実行を停止します。
4. `BudgetedExecutor.run()` を唯一の送信境界とし、SDK側の自動再試行も無効にします。外部エラーにはキー・ヘッダー・本文を含めず、アダプターで秘匿化します。
5. providerの実際の利用量に基づくJPYを返します。利用量や換算額が不明なら0と仮定せず例外にし、予約を保持します。
6. タイムアウト後に新しいrequest_idを発行して自動再送しません。確認済み未送信か、請求明細の手動照合が必要です。

`request_id` は永続的な音声ジョブIDに結び付けます。台帳の重複拒否と、アプリのジョブ再開を同一仕様にします。
台帳に音声ファイル本体やAPIキーは保存しません。成功済みジョブの結果再取得は音声履歴側で行います。
生成後にWAV保存が失敗しても、請求実績を削除して再送しません。課金済み／成果物未保存を別状態で表示します。

## 脚本・話者・キャッシュ

- `story_history.json` の版IDと `editorial.json` の確定記録を照合します。確定後に変わった脚本を黙って使いません。
- `page_dialogue.split_dialogue()` の `Utterance(speaker,text,raw,needs_review)` を再利用します。曖昧な話者は `needs_review` のまま、生成対象にしません。
- 名前ではなく既存の人物UUIDに声を割り当て、ナレーターは独立した固定IDを持ちます。参照画像・人物IDを再作成しません。
- 声設定、音声ジョブ、字幕タイムラインは独立ファイルで版管理します。既存story/schema_version: 1やartwork/compositionへ破壊的な追加移行をしません。
- 音声キャッシュキーはセリフ・読み・話者の声ID・モデル・速度等の設定・形式版を含む内容ハッシュにします。
- **作品全体のストーリー版IDをキャッシュキーに直接入れて全音声を無効化しません。** 版IDは生成元として別に記録し、変更したセリフだけ再生成します。
- キャッシュ再利用時はAPI送信・予算予約ともに0回。権利確認や声の許諾が失効した素材は再利用しません。

## 音声 → FFmpegの元の契約案（実装済み範囲は別紙）

音声の実尺・素材・字幕・生成元照合はPhase 3で実装しました。focusや出力別構成などはFFmpeg側で追加します。

| 領域 | 必要な項目 |
| --- | --- |
| ルート | format、独立schema_version、project_id、timeline_version_id、story_version_id、artwork_revision |
| 発話 | utterance_id、speaker_id、元セリフ・読み、音声ジョブID／request_id、要確認状態 |
| 音声素材 | asset_id、ローカル相対パス、SHA-256、実測duration_ms、sample_rate、channels、cache_key、権利情報 |
| シーン | scene_id、ページ・コマ、採用画像ID／SHA-256、start_ms/end_ms、画像のfocus、字幕領域 |
| 字幕 | utterance_id、表示文、start_ms/end_ms。SRTは実測音声を基準に整数ミリ秒から生成 |
| 出力別構成 | youtube_main／tiktok_vertical／youtube_shortsのシーン列・使用区間・短縮脚本・人間の承認状態 |
| 版の検査 | 元脚本・画像・音声・字幕設定のハッシュ、編集履歴。変更後はstaleとして再確認 |

経過時間を文字数やTTS見積から確定せず、WAVの実サンプル数・実尺を測ります。
発話を途中で切る縦型短縮は避け、既存音声の安全な区間と承認済みの短縮脚本を使います。
新しい短縮セリフのTTSが必要なら、FFmpeg内で送信せず音声工程へ戻し、別の予算確認を行います。

## FFmpeg側だけの依存

- `ffmpeg` と `ffprobe` はOSの任意バイナリです。pipの必須依存に追加せず、不在なら動画機能だけ止めます。既存の `video_prereqs` を再利用します。
- APIは使わず、採用画像・WAV・字幕をローカル合成します。main 1920×1080、縦型2種1080×1920、H.264/AACを実ファイルで検証します。
- 本編3〜5分、15〜25シーン程度と、縦型45〜75秒の尺は脚本・音声の確定後に検査します。足りない画像・尺を黙って補いません。
- `subprocess.run([...])` 等の引数配列、パス・サイズ・時間制限、一時出力、原子的な確定、中断・キャンセルを実装します。shell=Trueは禁止です。
- 横型を単純に切らず、focusと字幕セーフエリアで再構成し、重要部分が切れる場合は余白付きにします。
- BGM/SFXの権利、音声の許諾、投稿先の開示ポリシーを確認し、人間の公開前レビューを残します。公開操作は今回の範囲外です。

## 後続の受け入れテスト

TTS：3人物＋ナレーター、曖昧な話者、変更発話だけの再生成、キャッシュ課金0、料金期限切れ、タイムアウト予約保持、WAV実尺とSRT同期、成果物保存失敗。

FFmpeg：小さい自作PNG/WAVから3種類のMP4を実際に生成し、ffprobeで寸法・codec・FPS・音声・尺を確認します。欠損・不正パス・容量不足・プロセス中断・フォント・focus逸脱も検査します。

いずれも開発・CIは無料のモックとローカル処理だけです。実プロバイダー結合テストは、個別の明示承認と上限を伴う別作業にします。
