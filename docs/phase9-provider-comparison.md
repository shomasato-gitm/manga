# Phase 9：接続候補の比較

公式資料確認日：2026-10-26（日本時間）。公開資料の調査であり、実API・アカウント権限・最終請求・Mac実機の確認ではない。料金はUSD、Standard/通常リクエストの参考。割引・Batch・無料残高を最大見積へ先取りしない。Deprecated表示のあった候補は提供継続と後継を再確認するまで新規固定しない。

## テキスト

| 候補・正式ID | API方式 | 料金・無料枠 | 最大額・地域・条件・Mac |
| --- | --- | --- | --- |
| Gemini gemini-2.5-flash-lite | POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent | 100万token当たり入力$0.10、出力（thinking含む）$0.40。対象無料枠あり、アカウント/地域/モデル別制限内のみ | input/output/thinkingの上限と追加機能の非利用を仕様と照合するまで最大額未確定。日本は提供国に掲載、個別権限は未確認。無料枠はデータ改善利用/人手閲覧の条件あり。本人の入力権利/出力利用を確認。HTTPはCPU非依存だがmacOS12のTLS/実行環境は未検証 |
| OpenAI Responses gpt-5-mini / gpt-5-mini-2025-08-07 | POST https://api.openai.com/v1/responses。既存adapter再利用候補 | 100万token当たり入力$0.25、cached入力$0.025、出力$2.00。モデルのFree tier非対応 | ページにDeprecated表示。固定版の提供・権限、reasoningを含む出力上限、schema/parameterの再確認が必要。日本は提供国掲載。API契約と本人の権利確認が必要。HTTPのMac実機適合は未検証 |
| ChatGPT手動 | 本人がブラウザで送信し、既存prompt/JSON取り込みを使う | アプリからのAPI費用0。ChatGPT自身の契約/枠は別 | APIモデルID/上限と対応付けない。本人が送信範囲・権利・品質を確認。ブラウザ/ChatGPTのOS要件は別確認 |

追加参考：gpt-5.4-nanoは入力$0.20・出力$1.25/100万token（地域処理10%加算の記載）。gpt-4.1-miniも含め公開ページにDeprecated表示があるため、新規推奨の固定モデルにしない。モデル名だけを既存adapterへ入れて互換と判断しない。

資料：[Gemini料金](https://ai.google.dev/gemini-api/docs/pricing)、[GenerateContent](https://ai.google.dev/api/generate-content)、[地域](https://ai.google.dev/gemini-api/docs/available-regions)、[規約](https://ai.google.dev/gemini-api/terms)、[GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini)、[GPT-5.4 nano](https://developers.openai.com/api/docs/models/gpt-5.4-nano)、[GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)、[OpenAI提供国](https://developers.openai.com/api/docs/supported-countries)、[API契約](https://openai.com/policies/services-agreement/)。

[ChatGPTプラン](https://openai.com/chatgpt/pricing/)にはFreeと制限付き画像作成等の記載がある。有料プラン金額は取得本文に表示されず未確認。個別契約の実残枠も未確認。[消費者規約](https://openai.com/policies/terms-of-use/)と[API別請求](https://help.openai.com/en/articles/9039756-managing-billing-for-chatgpt-and-the-api-platform)を取得した。本人の利用目的と公開条件を確認し、API契約を代用しない。

## 画像

| 候補・正式ID | API・入力 | 料金・無料枠 | 未確定事項と採否 |
| --- | --- | --- | --- |
| Gemini gemini-3.1-flash-lite-image | generateContent。textと同意済み参照画像。初期は1K正方形1枚 | 100万token当たり入力$0.25、text/thinking出力$1.50、image出力$30。1K=1120token、画像出力部分のみ$0.0336。無料枠なし | 入力画像・thinking・複数候補・失敗時課金を含む最大量が未確認。既存adapterの本番停止を維持。Developer APIの地域/規約で確認し、Vertex料金を転記しない |
| OpenAI gpt-image-1-mini | /v1/images/generations。参照編集は/v1/images/editsという別能力。Responses画像toolをtext経路へ足さない | 100万token当たりtext入力$2、image入力$2.50、image出力$8。low/1024正方形の出力参考$0.005/枚。Free tier非対応 | Deprecated表示あり。組織確認・継続提供・参照入力と出力usageの最大値・失敗請求が未確認。adapter未実装で送信不可 |
| ChatGPT手動画像 | 本人が外部で生成し確認票と画像を取り込む | アプリAPI費用0。ChatGPT側費用/枠は別 | 現在使える推奨経路。外見・人物一致・権利確認と正式採用は別の人間操作 |

gpt-image-1.5も参考調査：text入力$5、image入力$8、image出力$32/100万token、low/1024正方形の出力参考$0.009。これもDeprecated表示があり、9-Cで現行モデルを再選定する。出力単価は入力込み総額や最大見積ではない。

資料：[Gemini料金](https://ai.google.dev/gemini-api/docs/pricing)、[Gemini規約](https://ai.google.dev/gemini-api/terms)、[OpenAI mini](https://developers.openai.com/api/docs/models/gpt-image-1-mini)、[OpenAI 1.5](https://developers.openai.com/api/docs/models/gpt-image-1.5)、[画像ガイド](https://developers.openai.com/api/docs/guides/image-generation)。一般的な提供国掲載は画像モデルの個別権限を保証しない。両HTTP方式にIntel依存はないがmacOS12での実接続・品質は未検証。

## 日本語音声

| 候補・正式ID | API・地域 | 料金・無料枠・最大額 | 適合性・条件 |
| --- | --- | --- | --- |
| VOICEVOX ENGINE | 許可済みloopbackの50021。GET /version・/speakers等のmetadata、POST /audio_query → POST /synthesis。engine/core version・speaker UUID・style IDを固定 | ローカルAPI従量課金0、電気/機材は別。engineと音声ライブラリ規約を確認して選ぶ | ENGINE 0.26.0にmacos-x64/arm64 assetsあり。一方現行ソフトQ&AはmacOS14以降。Intel Mac/macOS12のengine適合や古い版の安全性は未確認で保証しない |
| Amazon Polly | SigV4 POST https://polly.{region}.amazonaws.com/v1/speech。東京ap-northeast-1掲載。standard/neural、Mizuki/Takumi/Kazuha/Tomoko（engine別制限あり） | Standard $4 / Neural $16 /100万請求文字。Speech Marks別課金。新規AWS最大$200 credits等は条件付き。最大額はSSML UTF-8 bytesで文字数を保守的に上界化する既存方式を再利用 | 保存・再利用の説明あり。個別の商用権利・契約・日本語voice/engine/地域を本人確認。RESTはCPU非依存、Mac実機/IAM/請求は未検証 |
| 手動音声ファイル | 発話WAV importは未実装。BGM/SFX importは発話代用ではない | 取り込みAPI費用0、制作元ソフト/権利/費用は別 | 9-Bで発話ID/話者/脚本版/出典を結び既存WAV検査と実尺字幕へ。現時点で実装済みの安全な音声フォールバックは無料モック |

Polly公式ページにはStandard月500万文字・Neural月100万文字の最初の12か月の無料枠と、2025-07-15以降新規向け最大$200 credit、無料プラン6か月・credit期限12か月の記載がある。適用は契約・開始日で本人確認し、両方式が常に使えると仮定しない。実残枠は未取得。

VOICEVOXのソフト規約は商用・非商用利用とcreditを定め、各音声ライブラリの規約にも従う必要がある。例として、ずんだもん・四国めたん等は人物名付きcredit（VOICEVOX:ずんだもん等）と共通禁止事項の遵守が必要。他ライブラリへ一般化せず、実際のvoiceの規約URL/確認日/出典/必要creditを保存する。人物キャラクターの画像権利は音声規約だけで判断しない。

資料：[ENGINE](https://github.com/VOICEVOX/voicevox_engine)、[0.26.0 assets](https://github.com/VOICEVOX/voicevox_engine/releases/tag/0.26.0)、[公式OS条件](https://voicevox.hiroshiba.jp/qa/)、[ソフト規約](https://voicevox.hiroshiba.jp/term/)、[音源規約](https://zunko.jp/con_ongen_kiyaku.html)、[Polly料金](https://aws.amazon.com/polly/pricing/)、[API](https://docs.aws.amazon.com/polly/latest/APIReference/API_SynthesizeSpeech.html)、[地域/制限](https://docs.aws.amazon.com/general/latest/gr/pol.html)、[日本語Neural](https://docs.aws.amazon.com/polly/latest/dg/neural-voices.html)、[保存/再利用FAQ](https://aws.amazon.com/polly/faqs/)。

## 失敗・制限

[Gemini Billing](https://ai.google.dev/gemini-api/docs/billing)には400/500のtoken非課金とクォータ消費の記載がある。他の失敗・拒否・通信断を一律無料としない。[rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)はproject/tier/modelによるため固定回数を保証しない。OpenAI/Pollyもtier/region/voice制限と個別の失敗請求は未確認。不明時は予約を保持して照合まで再送禁止。

公開資料取得は権限検証ではない。今回、生成・モデル一覧・token数照会・VOICEVOX接続・実機インストールは未実施。契約・提供終了日・最大請求量の不足は未確認として接続不可にする。
