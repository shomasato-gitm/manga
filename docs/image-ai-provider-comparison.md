# 画像APIの公式比較（2026-10-24再取得）

実装前に公開公式資料をGETで再取得しました。生成API・トークン計数・課金テストは実行していません。
**今回は2-D1（無料モック・手動取り込み）を実装し、2-D2の実送信アダプターは実装しません。** 最大請求量を確認できないため、有料送信をロックします。

| 検討候補 | 確認できた公式料金・機能 | 未確認・採用判断 |
| --- | --- | --- |
| Gemini 3.1 Flash-Lite Image / 指定ID gemini-3.1-flash-lite-image | Google Cloud VertexのGlobal表は入力USD0.25/M、テキスト出力・reasoning USD1.50/M、画像出力USD30/M。脚注は入力画像1枚1120 token、1K出力1120 token（約USD0.034＝計算上0.0336） | ai.google.devのモデル・画像生成・料金・契約を403で取得できず、Gemini Developer APIのID・提供状態・入力上限・thinking等の総上限・商用条件は未検証。Vertex料金をDeveloper API料金と同一視しない。送信不可 |
| Gemini 3.1 Flash Image / 指定ID gemini-3.1-flash-image | Vertex Globalは入力USD0.50/M、テキスト出力・reasoning USD3/M、画像出力USD60/M。入力画像1120 token、1K出力1120 token（約USD0.067、計算上0.0672）。地域別加算あり | 同様にDeveloper APIの提供・許諾・最大総請求を確認できず、送信不可 |
| OpenAI GPT Image 2.5 Flare | 現行モデル資料は画像生成・編集に対応、品質low/medium/high/xhigh/max/auto。入力テキストUSD5/M、入力画像USD8/M、画像出力USD30/M | 公式はモデル・品質でtoken数が変わることと、計算機は見積であることを明記。取得資料から編集1回の厳格な最大入出力tokenを確認できず、送信不可 |
| OpenAI GPT Image 2.5 Sunburst | 同単価。編集精度重視と説明。既定snapshotは2026-09-08。画像参照・複数画像編集に対応 | 同単価でも1枚当たり同じ費用とは限らない。Flareとの人物一致・日本語プロンプト品質を実測していない。送信不可 |

OpenAIの現行ガイドは参照画像を使う編集、画像サイズ・品質・n・部分画像等を説明しています。Responses経由はメインモデル分も別途課金、直接Images APIには上記キャッシュ割引が適用されない旨も確認しました。
旧モデルの272／1056／4160 token等の表をGPT Image 2.5へ流用しません。n=1・非streaming・サイズ固定だけで総請求上限を証明したことにしません。
gpt-image-1は公式廃止一覧で2026-10-23終了を確認し、候補から除外しました。gemini-2.5-flash-imageのDeveloper API終了日は今回は取得不可のため独立検証できず、新規候補にしません。

## 500円との関係

次は**Vertexの画像出力部分だけ**の仮計算です。Developer APIの実行見積・予約額ではありません。
160円/USD・税1.10を仮定すると、24コマ＋基準3枚＝27枚で、Lite出力部分は約159.67円、Flashは約319.33円。
基準4種類×3人＋24コマ＝36枚なら約212.89円／425.78円です。テキスト・参照入力、reasoning、再生成、為替・税の実際、企画・脚本・音声の費用を含みません。
「500円で作品完成可能」という保証や、これを最悪請求額として実行する機能はありません。画像の品質・一貫性・実費は実API試験と人間の目視まで未確認です。

作品生涯500円・全作品月間5,000円は既存の同じ作品UUID・永続DBへ合算します。画像用に新しい500円枠を作りません。
モックと手動取り込みの呼出し・予約・課金は0です。ChatGPTの月額契約やGoogle AI StudioのUI試用をAPIクレジットとして扱いません。

## 商用利用・データ・一貫性

- OpenAI Services Agreement §4.1は法の許す範囲の出力所有権を利用者へ帰属、§4.3等は入力権利と利用条件を要求。画像や他人の人物・作品を送る権利を自動取得するものではありません。非侵害・独占・著作権成立は保証されません。
- OpenAIのAPIデータは既定で学習へ使用しない一方、abuse監視等の保持があります。store:falseをゼロ保持保証と扱いません。今回画像を送信していません。
- Google Cloud Service Specific TermsのGenerated Outputの権利規定を確認しましたが、取得できなかったDeveloper API／AI Studioの契約へ拡張しません。
- OpenAI自身も繰り返し人物の視覚的一貫性や構図制御の限界を説明しています。自動の顔一致率・商用可の認定を実装せず、採用時に人間が目視と素材権利を確認します。

## 公式出典・取得記録

- [OpenAI画像ガイド](https://developers.openai.com/api/docs/guides/image-generation.md)
- [Flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare.md) / [Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst.md)
- [画像編集API](https://developers.openai.com/api/reference/resources/images/methods/edit) / [料金](https://developers.openai.com/api/docs/pricing.md) / [廃止一覧](https://developers.openai.com/api/docs/deprecations.md)
- [OpenAI契約](https://openai.com/policies/services-agreement/) / [データ取扱い](https://developers.openai.com/api/docs/guides/your-data.md)
- [Vertex料金](https://cloud.google.com/vertex-ai/generative-ai/pricing) / [Cloud契約](https://cloud.google.com/terms/service-terms)
- 取得不可：[Gemini料金](https://ai.google.dev/gemini-api/docs/pricing) / [画像生成](https://ai.google.dev/gemini-api/docs/image-generation) / [契約](https://ai.google.dev/gemini-api/terms)。ネットワーク許可後も403で、未確認を確認済みと扱いません。

確認日付きの研究カードは`examples/image_ai/provider_research_20261024.json`。実行用PriceQuoteではなく、maximum_charge_verified:falseのままです。モデル・料金を製品コードへ固定しません。公式取得のURL・SHAは`verification/image_ai_official_sources.json`へ記録します。
