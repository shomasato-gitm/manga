# Phase 2-D3：Gemini画像APIの本番接続準備

確認日：**2026-10-24**。第一候補：`gemini-3.1-flash-lite-image`。
**結論：no-go。本番送信は無効のまま。実API呼び出し・実費は0。**
PR #10のマージを確認し、最新main `6d3ca3c018d2c261a10f3aa7fff5150e086f1225` から分岐した。

## 公式資料で確認できたこと

| 資料 | 取得・確認結果 | 適用範囲 |
| --- | --- | --- |
| [Developer料金](https://ai.google.dev/gemini-api/docs/pricing) | 未取得：プロキシのCONNECTが403。ネットワーク権限確認後の再試行も同じ | Developer APIの料金を今回確認済みとは扱わない |
| [画像生成](https://ai.google.dev/gemini-api/docs/image-generation) | 同上 | モデル別の1K実寸・参照・思考・枚数制限は未確認 |
| [billing](https://ai.google.dev/gemini-api/docs/billing) | 同上 | Developer APIの失敗請求・制限・課金範囲は未確認 |
| [GenerateContent](https://ai.google.dev/api/generate-content) | 同上 | 最大課金量とusageのモデル別の意味は未確認 |
| [モデル](https://ai.google.dev/gemini-api/docs/models)、[レート制限](https://ai.google.dev/gemini-api/docs/rate-limits)、[条件](https://ai.google.dev/gemini-api/terms) | 同上 | 正確な提供モデルID・利用資格・商用/データ/SynthID条件は未確認 |
| [Google公式SDK型定義](https://github.com/googleapis/python-genai/blob/main/google/genai/types.py) | 取得成功。candidate_count、max_output_tokens、ImageConfigの1K/1:1、thinking_budgetを確認 | 一般的な型。対象モデルのサポート・請求の契約ではない |
| [Vertex Global料金](https://cloud.google.com/vertex-ai/generative-ai/pricing) | 取得成功。入力0.25、テキスト/思考出力1.50、画像出力30 USD/100万token。1K画像1120token | 別サービスの参考値。Developer API料金に流用しない |
| [Google Cloud予算](https://cloud.google.com/billing/docs/how-to/budgets) | 取得成功。alerts-only予算は自動でハード上限を設けず、利用量報告の遅延を考慮するとの明記 | spend cap budgetもサービスの適用条件確認が必要。絶対的な請求上限の証明に使わない |

SDKのthinking_budget説明は「0 is DISABLED」「allowed ranges are model dependent」。
したがって対象画像モデルで0が受理され、思考課金がなくなるとは断定しない。
usageにも型上の任意項目や一部サービス非対応の注記がある。型だけで省略を0に置換しない。
今回の接続失敗は資料取得環境の制限であり、モデルが存在しない、Googleのサイト全体が停止している、という判定ではない。

取得URL・リダイレクト先・取得可否・日付・取得本文のSHA-256・短い引用を
[機械可読記録](../examples/image_ai/gemini_readiness_20261024.json)に保存した。
公式本文を取得できなかった資料を、取得・確認済みに偽装していない。

## 送信を解除できない具体的な条件

画面・診断JSONに次の10項目を表示する。

1. 正確なDeveloperモデルID、提供段階・資格・返却modelVersion。
2. candidateCount=1と1K/1:1による**1024×1024の1画像**、追加出力の扱い。
3. 人物参照画像のサポート、最大枚数・bytes・寸法・権利。
4. テキスト・全参照画像・プロトコルを含むサーバー保証の最大入力課金量。
5. 画像・付随テキストを含む最大出力課金量とmaxOutputTokensの関係。
6. モデル別thinking制御、最大思考量、maxOutputTokensへの算入・請求方法。
7. Developer APIの全モダリティ・追加料金・税の正しい価格範囲。
8. usageの全課金項目、合計・省略・内訳・キャッシュ・ツールの意味。
9. 安全拒否・中断・タイムアウト・429/5xx時の請求、レート・支出制限の範囲。
10. 商用利用、入力権利、データ保持・学習、生成物・SynthID等の条件。

いずれも今回のDeveloper資料からは立証できなかった。
料金カードの `maximum_charge_verified: true` やキー、環境フラグを設定しても解除しない。
旧no-go判定に加え、この確認記録の欠損・改変による形式不正・30日超過・未来日付・
未取得資料の確認済み化・別サービスの根拠による代用も拒否する。
資料診断だけでは個別の料金カード・作品・送信承認の検証を代行できない。

## 参考費用と最大費用

Vertexの**画像出力だけ**の参考計算は次のとおり。

```text
1120 / 1,000,000 × $30 = $0.0336
$0.0336 × 160円/USD × 税係数1.10 = 約5.91円
```

入力・参照画像・thinking・付随出力・追加料金・再生成・他工程を含まない。
Developer APIの見積額・最大額ではなく、5.91円を予約して本番実行することは禁止。
**最大費用は未確定（JSONではnull）。0円ではない。**

必要な最大値は、公式の全モダリティ最大課金量×各単価の合計に、為替・税・安全係数を掛ける。
既存PriceQuoteは最高入力/出力単価と保守的な最大量で予約するが、
その最大量自体のモデル別根拠が必要。平均値・文字数÷4・出力画像1120tokenだけで代用しない。
Google側の予算・アラート・クォータ・支出制限・事前入金は補助策であり、
この計算の不足を埋める絶対上限とはみなさない。

## 実装した安全対策

- 既存の「モデル・見積」「実API」画面に、確認済み事実・未確認10項目・参考額と最大額の違いを表示。
  JSONをダウンロードできる。新しい制作メニューは増やさない。
- `check_gemini_image_readiness.py` は公式確認記録と設定有無の**読取専用**診断。
  作品・予算DBを開かず、新規予約・HTTP送信を行わない。キーの値・長さ・ハッシュを出力しない。
- BudgetedExecutorの1実行contextにつき、画像HTTP送信権を**1回しか取得できない**。
  同じcontextのコピーを別スレッドで使っても再送できない。
- 予算予約・永続実行権取得の後にも、脚本・バイブル・作風・基準画像SHA・共有台帳の版/保存先を再確認。
  変更を検出したらHTTP送信前に停止。同じrequest_idを再利用しない。
- 画像があるのに出力usageが0、空でない入力が0、追加usageがboolなどの不正値も
  無料とみなさず請求不明として予約を保持。
- 一時的な通信・APIエラーによる再試行・モデル切り替えなし。
  500円生涯/5,000円月間の同じ共通台帳、既存候補・目視確認・権利確認・正式採用・保存復旧を維持。
- 既存ストーリー/人物UUID/基準・候補画像/音声/動画のJSON・ファイルを変更・移行・削除しない。

予約後の再確認で未送信停止した場合も、取得済み実行権は再使用しない。
予約を自動解放せず、既存予算画面で「送信0・請求なし」を手動照合してから別依頼を作る。
タイムアウト・usage不明・応答欠損も従来どおり手動照合が必要。

## Macでの動作確認

アプリ・生成処理を終了し、作品全体と共通台帳（`.initialized`も含む）をバックアップする。
PRはmain未マージなので、レビュー時は作業ブランチを使用する。

```bash
cd /実際の保存先/manga
git fetch origin
git switch feature/gemini-image-readiness
git pull --ff-only origin feature/gemini-image-readiness
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/check_gemini_image_readiness.py --output "$HOME/gemini-readiness-20261024-01.json"
```

**終了コード2は想定どおりのno-go**。終了コード1は資料・出力先のエラー。
既存ファイルを上書きしないため、再実行は別の出力名にする。キーは不要。
API・作品・台帳へのアクセスは0。もし既にキーが設定されていても、その有無だけを表示する。

無料の1枚生成→未採用→テストコードによる明示確認/採用→保存復旧の確認：

```bash
python scripts/verify_image_ai_live_fake.py --output "$HOME/MangaGeminiFake-20261024-01"
python -m streamlit run app.py
```

fake検証先は**存在しない新規フォルダ**にする。架空の14.30円予約/5.9224円換算は
そのフォルダの検証専用台帳だけの計算で、実際のAPI費用は0円。既存の作品/台帳を使用しない。
「イラスト制作 → AI画像生成・採用 → モデル・見積」で準備状況を確認できる。
生成計画を作った後の「生成・手動取り込み → 実API」でも同じ不足条件と送信禁止を確認する。

追加SDKなし。Python 3.11/3.12・Streamlit 1.40.2のテストはLinux上で実施し、Intel Mac実機は未検証。

## 本物の1枚に向けた残作業・代替案

1. `ai.google.dev` を取得できる環境で上の10項目の公式本文・URL・確認日を確認する。
   必要ならその資料を提供して再レビューする。APIキーは提供しない。
2. 上限・usage・thinking・参照・商用条件を確認し、必要なアダプター修正と料金カードを
   別PRでレビューする。カードだけで本番能力は解除しない。
3. 同じ永続台帳、低いプロバイダー側制限、環境変数キー、機能フラグを設定する。
4. **少額1枚の有料API実呼び出しへの別途明示承認**を得る。
5. 確定脚本・バイブルの顔/髪型/瞳/服装と基準画像を確認し、全参照の送信に同意する。
   1024×1024の1枚を最大費用確認→依頼保存→依頼IDごとの二段階目承認で実行する。
6. 生成は候補保存まで。既存の目視/権利確認と正式採用は別操作。人物一致は人間が確認する。

現時点の代替は、既存無料モック、またはChatGPT/AI Studioの画面で手動作成した画像を
既存の手動取り込みで候補化する方法。外部UIの料金はその契約に従い、API無料枠とは扱わない。
Vertexは別サービスなので、新たな条件・料金・上限確認と別PRが必要。自動切り替えは行わない。
