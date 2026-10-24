# TTS候補の公式資料比較

確認日：**2026-10-24（日本時間）**。公開ドキュメントのGETだけを実行し、音声生成APIは呼び出していません。
価格はUSD、無料枠を適用しない公開単価です。為替・税・個別契約・他のクラウドサービス費用は別に確認します。
日本語の対応は公式の言語・音声一覧で確認したもので、実音声の聴感比較は未実施です。

| 候補 | 公式ページで確認した単価 | 日本語・演技 | 商用利用条件の確認 | 今回の判断 |
| --- | --- | --- | --- | --- |
| Amazon Polly Standard / Neural | Standard $4／100万文字、Neural $16／100万文字 | ja-JP、Mizuki・Takumi・Kazuha・Tomoko。SSMLで速度・音量。Standardはwhispered対応、Neuralは非対応 | FAQは「Polly出力は利用者に帰属」、第三者テキストには必要な権利が必要と明記。AWS契約・Service Termsも適用 | **最初のアダプターに採用**。日本語Neural 3声＋Standard 1声を使い分けられ、入力文字数から最大額を計算でき、応答に文字数がある |
| Google Cloud Text-to-Speech | Standard / WaveNet $4／100万文字、Neural2 $16／100万文字、Chirp 3 HD $30／100万文字 | 公式一覧にja-JPのStandard・WaveNet・Neural2・Chirp 3 HD等。モデルによりSSML制約が異なる | Cloud契約・Service Specific Termsを確認。Customer Data / Applicationsの権利とサービス側IPを区別。特定の音声出力の再配布条件は導入前に契約を個別確認する | 安価な候補として維持。今回はアダプターを増やさず、実装と請求確認の経路を1つにする |
| OpenAI gpt-4o-mini-tts | 入力text $0.60／100万token、出力audio $12／100万token | 日本語を対応言語に掲載。instructionsで感情・抑揚・ささやき等を指定できる一方、声は英語向け最適化との記載 | Services Agreement §4.1はAPI入出力の権利を規定。**ChatGPT Voice Outputの商用禁止条項とAPI TTSを混同しない**。AI音声であることの明示も必要 | 出力audio tokenの請求上限を確実に制御する契約をこの実装で確立できないため採用しない。モデル・スナップショットの現行提供状況も導入時に再確認 |

Pollyが常に最安・最高品質という結論ではありません。今回の優先条件は、日本語対応、低い文字単価、500円枠に対する上限計算、利用量応答、再送なしの安全な保存です。
実音声の品質は別途承認された少量試聴で確認します。初期画面は引き続きAPIなしの確認用トーンです。

## Pollyで確認したAPI契約

- `POST /v1/speech`、Engineは`standard`または`neural`、LanguageCodeは`ja-JP`。
- PCMはsigned 16bit、little endian、モノラル。PCMのSampleRateは8000または16000。アプリは16000を使います。
- 同期APIの入力は全体6000文字・課金対象3000文字以下。SSMLタグは課金対象文字数へ含めません。
- アプリは読み上げ文3000文字以下かつSSMLのUTF-8全体4000バイト以下に制限します。予約は**タグを含むUTF-8バイト数**を文字数上限として扱い、過少見積を避けます。
- 応答の`x-amzn-RequestCharacters`を利用します。欠落・0・不正値を「無料」とみなさず、請求確認待ちにします。
- アダプター内の自動再試行はありません。HTTPリダイレクトも拒否します。

| VoiceId | Standard | Neural |
| --- | --- | --- |
| Mizuki | 対応 | 非対応 |
| Takumi | 対応 | 対応 |
| Kazuha | 非対応 | 対応 |
| Tomoko | 非対応 | 対応 |

怒り・悲しみはSSMLの速度・音量による演技プリセットです。感情専用モデルの自然さを自動保証しません。
Neuralのささやきは小声・低速の近似です。Standardでは専用の`amazon:effect name="whispered"`を使います。

## 料金見積の例

仮定：24発話、合計3000文字（日本語各125文字）、すべてNeural、換算上限160円/USD、税係数1.10、安全係数1.25。
160円は市場為替の取得値ではなく説明用の仮定です。

- 文字数だけの参考換算：3000 × $16 / 1,000,000 × 160 × 1.10 = **8.448円**。
- 同じ文字数へ安全係数を掛けた参考値：**10.56円**。
- この実装が予約する上限：通常演技のSSML全体が10440 UTF-8バイトになる例では、10440 × $16 / 1,000,000 × 160 × 1.10 × 1.25 = **36.7488円**。
- 実際の予約額は、読み・声・感情・SSML長から毎回計算します。共有台帳の作品残額500円・全作品月間5000円を超えるなら送信しません。
- 完全なキャッシュ再利用、確認用モックはAPI呼び出し0回・追加API費用0円です。

台帳の精算値は、提供元が返した文字数を設定済み為替・税で換算した**予算上の円換算額**です。最終請求書や実決済為替を取得した金額ではありません。
個別の最低料金・追加費用などがある場合、確認できるまで有料設定を承認しないでください。AWSの他サービス利用料をこの単価へ含めたとは扱いません。

## 公式出典

確認できた本文の条件を要約しています。契約の最新版・個別条件の最終判断は利用者が行います。

1. [Polly料金](https://aws.amazon.com/polly/pricing/)
2. [Polly SynthesizeSpeech仕様](https://docs.aws.amazon.com/polly/latest/dg/API_SynthesizeSpeech.html)
3. [Polly音声一覧](https://docs.aws.amazon.com/polly/latest/dg/available-voices.html)
4. [Polly SSML対応表](https://docs.aws.amazon.com/polly/latest/dg/supportedtags.html)
5. [Polly FAQ：出力所有権、保存と再利用](https://aws.amazon.com/polly/faqs/)
6. [AWS Service Terms](https://aws.amazon.com/service-terms/)：§50.3にはPollyで処理したAI Contentの利用・保存とAWS Organizationsによるオプトアウトの説明があります。未公開脚本を送信する前に確認してください。
7. [Google Cloud料金](https://cloud.google.com/text-to-speech/pricing/)
8. [Google Cloud音声一覧](https://cloud.google.com/text-to-speech/docs/list-voices-and-types)
9. [Google Cloud契約](https://cloud.google.com/terms)、[Service Specific Terms](https://cloud.google.com/terms/service-terms)
10. [OpenAI TTSガイド](https://developers.openai.com/api/docs/guides/text-to-speech)
11. [OpenAIモデルと料金](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)
12. [OpenAI Services Agreement](https://openai.com/policies/services-agreement/)、[Service Terms](https://openai.com/policies/service-terms/)

参考：[公式Polly SDKのAPI定義](https://github.com/aws/aws-sdk-go-v2/blob/main/service/polly/api_op_SynthesizeSpeech.go)、[VoiceId / Engine列挙](https://github.com/aws/aws-sdk-go-v2/blob/main/service/polly/types/enums.go)。
SDKは参照資料だけで、アプリの追加依存にはしていません。

確認日の料金スナップショット例は [examples/voice/pricing_20261024.json](../examples/voice/pricing_20261024.json)。
利用者確認フラグはfalseで配布します。公式条件・為替・税を確認してから設定し、有料実行は別の明示承認が必要です。確認日から30日を過ぎた料金は拒否します。
