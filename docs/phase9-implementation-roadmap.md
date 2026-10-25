# Phase 9-B / 9-C 実装計画

計画日：2026-10-26。9-Aは資料と読み取り専用診断のみ。Phase 8 UI、既存送信停止、料金カード/budget、採用処理は無変更。

## 推奨構成・順序

現在はChatGPT手動text/画像＋無料mockを維持する。9-Bでlocal音声と非対応環境用の発話WAV importを先に用意し、その後、無料枠/規約/品質を確認できた低コストGemini textを任意選択にする。無料を使えなくても有料へ自動変更しない。

Responsesは既存Serviceを使う代替だが、Deprecated表示モデルを無条件に固定せず、9-B開始時に現行ID/料金/parameterを再選定。画像は手動を既定に維持し、9-Cで一枚だけのadapterを評価。品質・料金・復旧の確認まで24枚一括/連続生成/自動採用を禁止。

## 9-B.1：単発実行と既存経路の補強

| 作業 | 再利用先 | 受け入れ条件 |
| --- | --- | --- |
| 能力・料金・送信範囲を凍結 | story_ai_pricing、PriceQuote、image_ai_live_planの方式 | 固定版/schema/reasoning/最大量/税/為替/資料期限が揃わなければ送信不可 |
| 1承認1送信 | 既存job/request、BudgetedExecutor、claim_provider_call | 有料draft/evaluation/improvementを別承認の単発requestにする。Pollyも1発話ずつ。並列/再起動/二度押しで承認再利用不可 |
| adapter入口のガード | ResponsesStoryAI、PollyVoice | 直接呼び出し、provider/model違い、二度目transportをフェイクで拒否 |
| 不明結果の復旧 | story_ai_engine.recover、VoiceService.recover、予算照合 | timeout/不正usage/保存障害後に再送しない。同IDで保存だけ再開。不明請求予約を自動解放しない |

旧ジョブの複数有料工程承認から切り分ける。既存mockの複数工程や旧履歴読込を壊さない。承認消費は後方互換な既存job拡張等を別レビューし、中央成果物DBへ移行しない。

## 9-B.2：ローカル日本語音声と手動WAV

1. 正規engine/core/voice library、配布元/バイナリhash、Intel/OS条件、商用/creditを確認。現行macOS14以降という案内とmacOS12の差を解消できなければ12で選択不可。x64 assetだけで適合扱いにしない。
2. 固定loopback allowlist、redirect拒否、local proxy遮断、timeout/サイズ上限。metadataのみの確認と生成を分離し、任意URL欄を作らない。
3. アプリ人物UUIDとVOICEVOX speaker UUIDは別IDとして対応付け、既存UUIDを置き換えない。engine/core/version/style・規約/creditを固定し、対象と本文を事前表示。audio_query→synthesisを実行して候補保存、確認/割り当ては別操作。
4. voice_settings/validate_voiceに新providerを後方互換追加。旧JSONは従来どおり。core更新を暗黙適用せずcache keyに含め、旧WAV/history/budgetを移行・削除しない。
5. 発話ID/話者UUID/脚本版へWAVを明示割り当てる公開importを追加。出典/権利/creditを必須にし、inspect_wav/ffprobe/実尺字幕へ接続。16kHz/16bit/monoへ明示変換し、変換前後SHAと元ファイルを保持。BGM/SFXを台詞へ流用しない。
6. 非対応/停止時はmockと手動WAVを本人が選択。有料Pollyへの切替禁止。mockを完成品質としない。

フェイクHTTP試験：外部URL/redirect/DNS/別port/認証URLの拒否、404/timeout/oversize/不正JSON/voice違い/WAV破損で停止。実engine試験は対応環境で本人が別途開始する。9-Aの試験には含めない。

## 9-B.3：低コストtext

- Gemini専用adapterと能力表。gemini-2.5-flash-lite等の提供/終了予定、無料枠、schema、thinking上限を再確認。無料枠は実枠/データ利用への同意を必須にする。
- Responsesは既存Service/candidate保存/採用を再利用。reasoning/strict schema/返却model ID/usageをフェイク確認。モデル一覧や認証確認のために生成しない。
- 企画1回→本人選択→脚本1回→本人候補確認→評価1回と承認を分ける。Automationの無料一括へ有料を加えない。
- 入力payload全体（system/schema含む）と公式上限でtoken上界を保証。最大額不明/未知課金を停止。grounding/tools/cache作成/background/batch/自動retryは初期対象外。
- 日本語JSON精度と8ページ24コマの整合のモック試験と実品質試験を区別。実費評価には上限・送信範囲・一回承認を別途必要とする。

## 9-C：画像を一枚だけ生成

1. 正式モデル/地域/参照画像/一枚制約/input/output/thinking/usage/失敗課金/規約を公式再確認。OpenAIも同条件。廃止表示モデルの価格だけで実装しない。
2. 根拠が全て揃った場合だけ、既存Gemini停止定数/manifest/readinessを更新する別レビュー。9-Cでも不足なら解除しない。flagや自己申告JSONで迂回しない。
3. ImageAIService.planで一コマ・脚本版・人物/バイブル/指示/配置を凍結。参照各件の送信同意と料金カード/body hashへ一度だけ承認。
4. 初期は1K正方形・一枚・品質固定・retry=0。共有budget/provider claim内で単発実行。自動補完・24枚・自動再生成・モデル昇格なし。
5. bytes/base64、MIME/寸法/容量/usageを検証してImageAIStoreへ候補保存。目視/権利確認後に別操作でArtworkStoreへ採用。失敗で旧候補/採用を消さない。
6. timeout/HTTP異常/usage未知/複数枚/寸法違い/保存失敗/見積超過をフェイク検証。画像/script/page/voice/video履歴と共有台帳保護を回帰検証。
7. 人物基準＋一枚の実品質/請求/復旧が本人確認されて初めて次の単発利用を検討。24枚一括の解禁は完了条件に含めない。

## 共通の試験・公開条件

Python3.11/3.12全pytestとAppTestで設定保持・承認失効・disabled理由・復旧先・人間採用分離を検査。UUID/旧JSON/history/budget/同期を保護。8ページ24コマの無料mockでPNG/PDF/音声字幕/3形式MP4/差分/再開を回帰検証。実素材品質・同期・公開可否は別の本人確認。main自動マージ・公開・同期・SNS投稿はしない。

## ユーザー側で必要になる操作

- 手動/無料/local/paidを本人選択。既定は手動で、paid flagだけでは送信しない。
- 正式モデル/料金/地域/規約/アカウント枠を確認し、キーは環境変数へ。チャットやJSONへ貼らない。
- 対応OSで正規VOICEVOXをインストール・起動、話者規約とcredit確認。macOS12の適合不明なら手動/mockを選ぶ。
- 各一回の送信対象/参照画像/最大見積/残予算を承認。不明結果はprovider明細と予算/成果物履歴を照合。
- 比較・目視/試聴・権利確認・正式採用、作品全体の最終品質確認を本人が行う。
