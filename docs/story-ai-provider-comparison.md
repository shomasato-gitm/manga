# 企画・脚本生成APIの公式比較（2026-10-24）

実装前に公開資料をGETで確認しました。有料生成・トークン計数API・実モデルの比較試験は実行していません。
日本語漫画の脚本品質を直接保証する公式比較指標は確認できず、文学的な品質は未評価です。日本語の指示と実作品による人間の確認が必要です。

| 候補 | 公開単価USD / 100万token | JSON・日本語脚本・契約 | 判断 |
| --- | --- | --- | --- |
| OpenAI GPT-5.6 Luna | 通常入力0.20、出力1.20。272K超の入力は全体の入力2倍・出力1.5倍。キャッシュ書込は入力1.25倍 | ResponsesのStructured Outputs対応。低費用向け汎用テキストモデル。日本語脚本の聴感／創作比較は未実施。Services Agreement §4.1は法の許す範囲で出力所有権を利用者に帰属、§4.3等の入力権利・利用条件も適用 | 最初の接続先の設定例。model ID・単価はコードに埋め込まず、確認済み料金カードで指定 |
| OpenAI GPT-5.6 Terra | 通常入力2、出力12。同様の長文・キャッシュ書込加算 | 公式は知能と費用のバランスを重視。Structured Outputs対応。日本語漫画でLunaより優れるという実測はない | 上限見積が500円枠に入るか確認が必要。自動昇格しない |
| Google Cloud Vertex Gemini 3.5 Flash-Lite | Global入力0.30・出力2.50、Non-global 0.33・2.75。無料枠・期間限定クレジットは除外 | Structured output / response schema対応。Cloud Service TermsのGenerative AI Servicesは新たなGenerated OutputのIP所有権をGoogleが主張しない旨を規定。モデル・地域別条件とデータ取扱いの確認が必要 | 代替候補。今回OAuth／Vertexアダプターは追加せず、予算境界を1経路に限定 |

GPT-4.1 mini/nano、GPT-5 mini/nano、GPT-5.4 nanoも調べましたが、取得した現行モデル資料にDeprecatedと記載されているため、新規採用の設定例にしません。
Claudeの料金資料とGoogle AI Studioの資料は通信制限で取得できず、確認済みの比較として記載していません。Googleは取得できたVertexの公式資料で比較しています。

## 上限計算を平均token数にしない

Responsesにはmax_output_tokensがあり、可視本文とreasoningを含む全生成tokenを制限します。
一方、通常のcreateには入力tokenの最大値を指定する引数がなく、文字数÷4だけではtools/schemaなども含む請求上限を保証できません。
今回のアダプターはツール・過去会話・検索・コンパクションを使用せず、**公式モデルの最大入力token数全体**と指定出力上限で予約します。
GPT-5.6 Lunaの公式最大入力は922,000 token、出力最大128,000。設定例の出力上限は16,384です。

長文・キャッシュ書込を含む安全側の有効入力単価0.50、出力1.80を例にします。
仮定160円/USD・税1.10・安全係数1.25なら、1呼出しの予約上限は107.908064円。
企画1回＋初稿／初回評価2回＋改善／再評価5組10回の**13回の理論上限は1,402.804832円**です。
実際には短い依頼が想定されますが、平均費用と上限保証を混同しません。

実行時は共通の作品累計500円／全作品月間5,000円に加え、依頼単位の上限枠を適用します。
各呼出しの前に残額を確認し、足りなければ未送信で停止します。5回は上限で、全反復の完走を保証するものではありません。
利用者には単発最大額・全工程の理論最大額・実行上限を別々に表示します。
精算も応答の入力／出力tokenへ安全側単価と設定為替・税を適用した予算換算で、割引を反映した最終請求書の取得ではありません。
不明な料金やモデル、欠落したusageは0円として進めず停止します。

## 公式出典

- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna.md)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra.md)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs.md)：拒否や未完了にも対応し、ローカル検証を省かない。
- [Counting tokens](https://developers.openai.com/api/docs/guides/token-counting.md)：文字数推定の限界、output上限は不可視tokenも含む。
- [公式Python SDKのcreate引数](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_create_params.py)：資料だけ参照し、追加依存にしない。
- [OpenAI Services Agreement](https://openai.com/policies/services-agreement/)・[Service Terms](https://openai.com/policies/service-terms/)
- [OpenAIデータの取扱い](https://developers.openai.com/api/docs/guides/your-data.md)：APIデータは既定で学習に利用しないが、abuse監視等の保持がある。store:falseをゼロ保持保証と扱わない。
- [Vertex料金](https://cloud.google.com/vertex-ai/generative-ai/pricing)
- [Vertex構造化出力](https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/control-generated-output)
- [Google Service Specific Terms](https://cloud.google.com/terms/service-terms)

契約は利用者の地域・アカウント条件に従います。出力所有権は著作権成立・非侵害・独占性・商用配信先の承認を保証しません。
最新料金・モデルの提供状況・商用条件・最大請求量は30日以内の確認日とともに設定へ記録し、未確認フラグのまま有料実行しません。
