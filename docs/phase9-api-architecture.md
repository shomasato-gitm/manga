# Phase 9-A：AI接続の実装調査と共通契約

調査日：2026-10-26（日本時間）。基点main：59da1ac（Phase 8 PR #15マージ済み）。このPRは設計資料・独立した読み取り専用診断・オフラインテストだけを追加する。既存Service、制作画面、採用、予算、スキーマ、同期は変更しない。実API試験は行わない。

## 1. 既存実装の分類

「実API送信可能」はコード上の経路がある意味で、認証・提供地域・モデル互換性・実課金・生成品質を確認済みという意味ではない。今回の診断から送信可能にはしない。

| 機能 | 分類 | 実装・阻害条件 |
| --- | --- | --- |
| OpenAI Responsesの企画・脚本 | 実API送信可能な経路あり／料金・権限・設定確認待ち | story_ai_provider.ResponsesStoryAIの固定HTTPS POST。StoryAIService.create_jobでMANGA_ENABLE_PAID_STORY=1、料金カード、見積承認、共有予算を要求。有効なモデル・単価の既定値なし。実接続未検証 |
| Gemini text | 料金・権限・設定確認待ち／未実装 | StoryAIService.estimateのproviderはmock / openai-responsesのみ。adapter注入可能というだけではGemini対応ではない |
| 脚本モック | 無料モックのみ | story_ai_mock。正式採用・脚本確定は別操作 |
| ChatGPT text | 手動連携のみ | プロンプト取得・JSON取り込み、既存の検証・比較・採用。ブラウザ契約とAPI予算は別 |
| Gemini画像 | 本番送信が明示的に無効 | image_ai_live_pricing.NETWORK_VERIFIED=False、旧調査manifest、本番readiness、環境flagの複数ガード。対象gemini-3.1-flash-lite-image。新調査JSONを旧manifest/cardへ流用しない |
| OpenAI画像 | 料金・権限・設定確認待ち／未実装 | Images API adapterなし。Responses text adapterは画像対応ではない |
| 手動画像・画像モック | 手動連携のみ／無料モックのみ | manual_image_ui、Automation.import_manual、ImageAIService.create_job/run/review/adopt。確認票・SHA・素材token、目視確認と別操作の正式採用を維持 |
| Amazon Polly | 実API送信可能な経路あり／料金・権限・設定確認待ち | voice_polly.PollyVoiceのSigV4 POST。VoiceService/voice_jobsが料金・環境flag・見積承認・共有予算を検査。AWS実接続・請求未検証 |
| VOICEVOX ENGINE | 料金・権限・設定確認待ち／未実装 | VoiceServiceはmock / aws-pollyのみ。ローカル接続・話者一覧・規約・WAV変換が必要 |
| 発話用手動WAV | 手動連携として必要だが未実装 | WAV検証・保存・試聴は存在するが発話WAVの公開importメソッドなし。video_ui_mediaのBGM/SFX取り込みを発話用と混同しない |
| 無料音声 | 無料モックのみ | MockVoiceは確認用トーンで、日本語音声・公開品質として完了にしない |

### 現行と新しい安全条件の差分

- StoryAIService.estimateは脚本生成・評価・改善を最大2+2*max_cycles回として承認する。voice_jobs.generateも複数発話を一つの承認で扱う。9-Bでは有料単発経路を分離し「1承認1リクエスト」にする。既存モックの複数工程処理は維持。
- BudgetedExecutorは永続request_idとexecution claimで同依頼の再実行を拒否する。Gemini adapterはclaim_provider_callも呼ぶ。Responses/Pollyの低水準transportには同等の強制境界がない箇所があり、Serviceから呼ぶ規約に依存する。9-Bで直接呼び出し拒否を補強する。このPRでは変更しない。
- Responses payloadはreasoningとstrict schemaを常に含む。モデル名の変更だけでは新モデルのparameter互換を保証しない。
- PriceRatesはプロバイダー別の公式所有者を自動認証するものではない。新しい調査JSONは実行用カードと別formatであり、料金カードへ自動変換しない。
- BudgetLedger constructorは初期化・整合性管理を行う。読み取り専用診断でopen_budgetを呼ばず、残予算は未取得とする。

## 2. 共通の実行契約（9-B/9-C設計、未実装）

~~~mermaid
stateDiagram-v2
    [*] --> Draft
    Draft --> Validated: 接続方式・能力・入出力を照合
    Validated --> Quoted: 最大課金量と公式料金が確定
    Quoted --> Approved: 対象・送信範囲・上限へ個別承認
    Approved --> Reserved: 共有予算でrequest_idを予約
    Reserved --> Sending: 永続claimを一度だけ取得
    Sending --> ValidatedResult: model・usage・結果を検証
    Sending --> Uncertain: timeout・usage欠損・異常応答
    ValidatedResult --> Saved: 既存Store保存・費用記録
    ValidatedResult --> ArtifactPending: 保存失敗
    ArtifactPending --> Saved: 保存済み応答から復旧
    Uncertain --> Reconciled: 明細と応答の人間照合
    Reconciled --> Saved: 利用できる応答を保存のみ
    Saved --> HumanReview: 正式採用は別操作
~~~

UncertainからSendingへの遷移は作らない。新たな生成が必要なら照合後に新しい見積・承認・request_idを作る。公式がリトライを推奨しても自動再送しない。

共通メタデータ案：project_id、既存工程/job_id、request_id、provider/model固定版、capability、source_token、送信payload SHA、参照画像ID/SHA、料金根拠ID/SHA/期限、最大利用量・円額・為替・税・安全係数、承認hash/消費状態、execution claim、provider request ID、usage、結果SHA/保存先/状態。キー・認証ヘッダー・生の例外は保存しない。

共通操作：describe_capabilities → validate_input → quote_maximum → budget_preview → approve_once → execute_once → validate_result → persist_result → reconcile。今回、送信APIは追加しない。

無料枠・ローカルでは、0円を証明できる条件と既存ジョブ内の実行claimを使い、有料用PriceQuoteへ0円を渡さない（現行は正額のみ）。有料の可能性がある処理を0円と宣言してBudgetedExecutorを迂回しない。無料枠の適用不明は停止。ローカルで課金台帳を作らない。

| 個別adapter | 入出力・検証 | 正本と承認 |
| --- | --- | --- |
| Text | 工程request → strict JSON、model、request ID、input/output usage。既存story_ai_schemas/models/resultsの正規化を再利用。reasoningを出力課金に含め、toolsなし | AIStoryStoreへ候補保存、StoryStoreへの正式採用を別操作 |
| Image | 確定脚本版・人物UUID・バイブル・指示・同意済み参照 → 画像1枚、MIME/寸法、modality別usage。ImageAIService.planとcurrent(hashed=True)を再利用。初期はbytes/base64のみ、任意URL取得禁止 | ImageAIStore、レビュー後に別操作adopt。ArtworkStoreの画像履歴を保持 |
| Voice | 発話ID・読み・話者UUID・engine/core/style・設定 → PCM/WAV、実尺、出典/credit。inspect_wav/ffprobe、明示的な16kHz/16bit/mono変換 | VoiceStoreに追記、確認/割り当てを分離。実尺からvoice_timelineで字幕 |

既存Storeを成果物の正本とし、中央成果物DBを増やさない。承認消費を各既存ジョブと予算claimに結ぶ。旧JSONの破壊的な一括書き換えをしない。VoiceStoreの新provider validator等は9-Bで旧fixtureとともにレビューする。

## 3. ダッシュボード・自動進行

production_dashboard.dashboardは保存済み素材から派生状態を表示する。接続可否と作品完成を分け、キー設定や候補生成を完了数へ加算しない。Automationの無料処理を維持し、有料text・画像・Pollyを一括無料実行に追加しない。ローカル音声も規約・対象確認前には候補提示のみ。

脚本確定・人物同定・基準画像採用・コマ確認/採用・権利判断は人間の停止点のまま。変更後は既存token/fingerprintで下流を再確認。同期・SNS投稿は自動化しない。

## 4. 設定・管理 → AI接続管理（画面設計のみ）

今回はルート・画面を追加しない。将来はText/Image/Voiceカードと共通の予算・復旧領域を置く。

| 項目 | 表示・操作 |
| --- | --- |
| 接続方式 | 手動が既定。local/free/paidを明確に区別し、自動切り替えなし |
| 利用可否 | 未実装・資料不足/期限切れ・権限不明・不明請求・環境非対応を個別表示。HTTP成功だけで生成可能としない |
| キー | 設定あり/なしのみ。値・長さ・一部・hash非表示、JSON保存なし |
| モデル/料金 | 正式ID/固定版・公式確認日・単位・無料条件・廃止表示。参考価格と実行用最大見積を別欄 |
| 送信範囲 | プロンプト全文、画像/参照画像のID/SHA/寸法/枚数と送信先を本人確認 |
| 最大見積/残予算 | 不明なら送信不可。作品累計/全作品月間の残額、予約/不明請求を表示。未取得を500/5000円残と表示しない |
| 実行状態 | 課金は既定OFF。前提充足と今回承認後に単発操作。実行中の競合操作を停止。接続診断で生成しない |
| 手動へ戻る | 現行prompt/JSON/画像取り込みへ。発話WAVは9-B実装まで未提供と明示 |
| 復旧 | 既存予算管理と脚本/画像/音声復旧へ。照合と保存復旧を区別し、再送ボタンを置かない |

## 5. 今回の読み取り専用診断

~~~bash
python scripts/diagnose_api_safety.py
~~~

調査JSON、Gemini停止定数、環境変数の設定有無だけを読む。日本時間の調査日と比較し、未来/30日超過をブロック。接続・生成・token照会・認証検証・作品読込・台帳作成/予約/保存/復旧はしない。終了0は診断完了で接続可ではなく、不正資料は2。JSONを全confirmedにしても実行権を出さない。

残予算と到達可能性は未取得。既存アプリの有料flagを強制OFFにするツールではない。既存設定が有料なら別途確認する。ループバック判定は設計のオフライン検証で、HTTP adapterは9-Bに残す。
