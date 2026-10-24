# 画像実APIの比較・接続判断（2026-10-24）

**判断：送信不可（no-go）。** 第一候補の Gemini Developer API
`gemini-3.1-flash-lite-image` のプロトコルを独立アダプターで実装するが、
本番送信は無効にする。公式の最大入力・画像出力・thinking課金量を立証できない。
料金カードの編集、キー、環境フラグだけでは解除できない。OpenAIへの自動切替もしない。

| 候補 | 確認できた公式料金（USD / 100万token） | 参照・日本語・一貫性 | 判断 |
| --- | --- | --- | --- |
| Gemini 3.1 Flash-Lite Image | Vertex Global: 入力0.25、テキスト出力/thinking 1.50、画像出力30。1K画像1120token = **出力だけ0.0336ドル** | Developer APIのモデル別制限・複数参照の仕様と日本語品質は未確認 | 第一候補。ただしVertex料金をDeveloper API料金として流用しない |
| Gemini 3.1 Flash Image | Vertex Global: 入力0.50、テキスト出力/thinking 3、画像出力60。1K出力だけ0.0672ドル | 上位比較用。人物一致の実画像評価は未実施 | 未接続 |
| GPT Image 2.5 Flare / Sunburst | テキスト入力5、画像入力8、画像出力30。Images APIではキャッシュ割引を仮定しない | 公式にテキスト・画像入力、生成・編集、品質選択に対応。Sunburstは精密編集を訴求。日本語・人物一致の実測は未実施 | token単価は確認。画像ごとの最大総token量の証明が不足し未接続 |

1ドル160円・税係数1.10ならGoogleの画像出力部分だけで約5.91円/11.83円。
入力・思考・追加出力・失敗・再生成・為替変動・他工程を含まず、見積上限ではない。
ChatGPT/AI Studioの月額プランはAPI無料枠ではない。

## この環境での一次資料の再取得

同日のHTTP GETのみ。生成APIへのPOSTは行っていない。

- [Google Developer料金](https://ai.google.dev/gemini-api/docs/pricing)、
  [画像生成](https://ai.google.dev/gemini-api/docs/image-generation)、
  [REST](https://ai.google.dev/api/generate-content)、
  [利用条件](https://ai.google.dev/gemini-api/terms)：環境の接続制限で取得不可。
  モデルの提供可否、最大課金量、商用条件、学習・保持条件、SynthID、参照上限は未確認。
- [Vertex料金](https://cloud.google.com/vertex-ai/generative-ai/pricing)：取得成功。
  **別サービス**の参考値。Developer APIの料金・契約・上限を証明しない。
- [Google公式SDK型定義](https://github.com/googleapis/python-genai/blob/main/google/genai/types.py)：取得成功。
  candidate_count / max_output_tokens / image_config / usageの型は確認できるが、
  一部の型にはGemini API非対応の注記もあり、対象モデルの実対応・課金上限を証明しない。
- [OpenAI料金](https://developers.openai.com/api/docs/pricing)、
  [画像ガイド](https://developers.openai.com/api/docs/guides/image-generation)、
  [Images edit](https://developers.openai.com/api/reference/resources/images/methods/edit)、
  [Flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)、
  [Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)：取得成功。
  可変サイズ・品質とusageによる請求で、旧モデルのtoken表を2.5の最大値に流用しない。
- [OpenAI契約](https://openai.com/policies/services-agreement/)、
  [データ利用](https://developers.openai.com/api/docs/guides/your-data)：取得成功。
  法律上可能な範囲の出力権利、入力権利の責任、APIデータの既定非学習を確認。
  不正利用監視ログは通常最大30日（例外あり）。独占性・著作権成立・第三者権利適合を保証しない。

Googleの認証は環境変数のAPIキーをヘッダーに渡す設計。モデル別レート制限、
タイムアウト時の請求と最大額、thinking制御、画像枚数保証、返却modelVersion、
usageの全課金項目、商用・データ・透かし条件の確認が解除の前提。
OpenAIのレート制限も利用階層依存で、タイムアウトを無料とは扱えない。
すべての人物一致・人体・構図・権利は人間がレビューする。

## 解除に必要な別作業

1. 公式Developer資料を再取得し、モデルID・1枚制限・参照制限・入力/出力/思考の
   **サーバー側最大課金量**とusageの意味を確認する。
2. dated JSONとコードの能力判定をレビュー付きPRで更新する。カードの自己申告だけでは解除しない。
3. 為替・税・安全係数を含む共有予算の最大予約、プロバイダー側支出制限を設定する。
4. 少額1枚の実試験についてユーザーの別途明示承認を得る。キーと個々の参照画像送信同意を設定する。

ローカル予算は請求上限の契約保証ではない。今回の実費は0円で、fakeの精算値は
テスト専用台帳の計算結果に過ぎない。
