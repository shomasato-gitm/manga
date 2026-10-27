# Phase 9-B3：Gemini 3.5 Flash-Lite

確認日：2026-10-27（日本時間）。新規画面の推奨は`gemini-3.5-flash-lite`。
既存の2.5設定・依頼・履歴は自動で変更しない。2.5の404時も3.5へ自動再送しない。

## 公式に確認した範囲

|項目|3.5の設定・根拠|
|---|---|
|正式ID・上限|Stable `gemini-3.5-flash-lite`。入力1,048,576、出力65,536 tokens。[モデル](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)|
|API|固定HTTPSのv1beta `models/<ID>:generateContent`へ1回だけPOST。認証は`x-goog-api-key`ヘッダー。[API](https://ai.google.dev/api/generate-content)|
|Standard料金|100万tokensあたり入力$0.30、出力$2.50（思考分を含む）。無料欄もあるが、実アカウントの適用・残枠は別確認。[料金](https://ai.google.dev/gemini-api/docs/pricing)|
|思考|`thinkingConfig.thinkingLevel=minimal`。完全OFFではない。maxOutputTokensは思考と回答の合計に対する上限。[思考設定](https://ai.google.dev/gemini-api/docs/generate-content/thinking)|
|構造化出力|3.5対応。公式REST例に合わせ`responseFormat.text`のmimeType=`APPLICATION_JSON`とschemaを使用。[構造化出力](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)|
|無料枠の条件|プロジェクト単位の適用・実際の利用枠はAI Studioで本人が確認。固定のRPM/RPDを推測で組み込まない。[利用枠](https://ai.google.dev/gemini-api/docs/rate-limits)|
|データ・利用条件|無料利用で送った内容が製品改善等に使われる場合がある。機密・個人情報を送らず、利用目的・送信範囲を本人が確認する。[規約](https://ai.google.dev/gemini-api/terms)|

[機械可読な確認記録](phase9b3-gemini35-evidence.json)は資料であり、実行許可・予算台帳・作品設定の正本ではない。
検索、Maps、明示キャッシュ、画像、音声、Batch、Flex、Priorityは使わない。Standardの既定動作を使い、非Standardの応答tierが報告された場合は料金を確定せず照合待ちにする。

## 実装の分離と互換性

モデルIDの調査では、送信先・料金/上限・診断・UIにあった固定値をモデル設定へ集約した。
残る2.5 IDは`gemini_models.py`の互換エントリーと旧ひな形・履歴向けの名前だけである。
`scripts`にテキストモデルの送信先固定はなく、画像アダプターのthinkingBudgetは別機能なので変更していない。

- `manga/gemini_models.py`が対応済みID・入力/出力上限・Standard下限単価・思考方式・出力形式を保持する。任意IDやURLは受け付けない。新モデルの追加には公式根拠とテストが必要。
- `story_ai_gemini.template(model)`はモデル別の料金JSONを作る。引数なしの旧APIは2.5互換を維持し、新規UIは3.5を明示指定する。`MODEL`/`INPUT_LIMIT`や診断の旧定数は互換名だけで、送信先の決定には使わない。
- 新しいひな形は[gemini_35_text_pricing_template.json](../examples/story_ai/gemini_35_text_pricing_template.json)。`thinking_level: minimal`を使う。旧2.5の`thinking_budget: 0`と混ぜない。モデル名だけを置換した旧料金設定は拒否する。
- 新規画面は3.5、保存済み2.5作品は2.5を表示する。モデル選択だけでは保存しない。選択とJSONのモデルが異なれば保存・診断を拒否する。確認フラグや無料枠を別モデルへ自動で引き継がない。
- 実行先・実行権・本文SHA・最大利用量・返答モデル・usageを同じモデルに結び付ける。返答モデルが違う、usageがない、料金tierが違う場合は成功・無課金とせず停止する。
- 通常の企画・脚本・評価・改善は既存Service/Storeと同じJSONスキーマを使う。object/array/string/integer/number/null、required、additionalProperties、enumが公式対応範囲にあることを確認した。ページ数・番号・人物・評価等の既存検証と人間の正式採用は残す。
- 3.5だけRESTの推奨responseFormatを使い、2.5の送信形式はresponseMimeType/responseJsonSchemaを保持する。両方式を同じリクエストへ混在させない。
- 3.5の診断は固定文への`OK`、2.5は従来のJSONテキストを検査する。診断は構造化出力や脚本品質を保証しない。
- 新しい診断イベントにはモデルを記録する。モデル欄のない旧診断は2.5として元の台帳・応答と照合し、3.5として復旧しない。古い依頼は保存済みcardのモデルを使い、設定変更後も自動で再価格設定・移行しない。
- 既存作品JSONのトップレベルスキーマ、作品ID、採用データ、既存の2.5ひな形、音声・画像・同期・無料モック・手動連携は変更しない。

## 予算・承認

作品累計500円/全作品月間5,000円の共通台帳をそのまま使う。provider名も従来の`gemini-text`で、2.5と3.5を別枠にして上限を迂回しない。
無料モードでもStandard単価による正の最大額を予約し、usageを保守換算する。これはGoogleの最終請求書とは別で、無料枠消費や実請求0円の証明ではない。

最大見積は平均文字数から小さく推測せず、モデルの全入力幅と指定した出力上限で算出する。
例として為替150円/USD・税1.10・安全係数1.25を仮定すると、診断（出力256）は65.012640円、通常生成（出力16,384）は73.328640円を予約する。
この仮定を本人の支払条件へ無断適用しない。ひな形の為替・税・税の説明は未入力で、料金日付・権限・規約・無料枠確認も未確認なので、そのまま送信できない。

3.5は思考分をcandidatesTokenCountに足して費用と整合性を確認する。maxOutputTokens到達時は回答が途中でも思考分が発生し得る。
途中の回答、不正JSON、要件不一致は正式採用しない。出力上限や思考レベルを自動で上げず、再生成にも別承認を必要とする。
403/404/429/タイムアウト等でモデル・料金・無料/有料を自動切替しない。請求不明の予約は維持し、[既存の手動照合](gemini-diagnostic-recovery.md)まで再送しない。

## Mac：無料枠での初回診断・企画・8ページ24コマ脚本

ユーザー報告として、Macから3.5のv1beta generateContentがHTTP 200/OKで応答し、2.5は404だった。certifi設定済みという事実を記録する。
これは今回のアプリ内の生成・無料枠・料金・品質の動作確認と同一ではない。CodexはこのAPI接続を再実行していない。

1. アプリを停止し、既存データと共有台帳をバックアップする。`MANGA_DATA_DIR`と`MANGA_BUDGET_DB`を元のまま使う。未確定請求があれば先に本人が照合し、不明ならここで停止する。台帳を作り直さない。
2. 既存のPython 3.11/3.12仮想環境でブランチを起動する。

   ```sh
   git fetch origin
   git switch feature/phase9b3-gemini35
   python -m pip install -r requirements-dev.txt
   export SSL_CERT_FILE="$(python -m certifi)"
   export MANGA_ENABLE_TEXT_API=1
   python -m streamlit run app.py
   ```

   `GEMINI_API_KEY`は既に設定した安全な環境から読む。キーを画面・JSON・スクリーンショット・コマンド履歴へ貼り付けない。`MANGA_ENABLE_PAID_STORY`は無料テストのために有効化しない。SSL検証を無効にしない。
3. 専用テスト作品を新規作成し、必要な架空人物と8ページの制作条件を保存する。既存作品の正式脚本を上書きして試さない。
4. 「企画・脚本 → AI企画・ストーリー → 制作条件 → Geminiの接続・無料枠・料金設定」で3.5を選び、ひな形を取得する。2.5履歴のある作品では勝手に切り替わらないので、3.5を本人が選ぶ。
5. 正しいGoogleの`test`プロジェクトを選び、3.5の無料枠適用・残枠・請求設定・データ利用条件・公式料金を確認する。JSONの確認日・為替/税・確認フラグを記入する。診断前はaccount_access_reviewed=falseのままでよい。free_tier_reviewedと確認日は本人の確認に基づく。
6. JSONを読み込み「通信なしで設定を診断する」を実行する。これだけではAPIへ送らない。選択モデルとJSONが一致すること、キー値が出ないこと、不足項目が解消したことを確認する。
7. 初回診断のモードを「無料枠は本人確認済み」にし、残予算・3.5モデル・思考minimal・固定文・最大費用を確認する。チェックと実行ボタンで**この診断1回だけ**承認する。HTTP・返答OK・モデル・usage・保存・台帳を確認する。失敗時は連打せず照合する。
8. アクセスと利用条件を本人が確認できた場合だけaccount_access_reviewedをtrueにし、通常生成用のJSONを明示保存する。診断成功ではこのフラグを自動更新しない。
9. 生成方法を「確認済み無料API」、AIをGeminiにし「企画案の生成・比較」へ進む。モデル・単価・最大入力/出力（思考込み）・送信範囲・残予算を見て、1回の外部送信を承認し「企画候補を1回生成」。候補を比較し、脚本に使う1案を明示選択する。選択と正式脚本の採用は別。
10. 「脚本制作・自動改善」で新しい1回の承認を行い「脚本候補を1回生成」。8ページ24コマ、描写・セリフ・ナレーション・人物・引きを確認する。自動評価や改善は続けて送信せず、次の承認待ちで停止する。
11. 評価・改善を行う場合も各1送信を別承認する。候補比較、正式採用、最新脚本の最終確定は人間が操作する。生成候補やテスト成功だけで完成・公開品質にはしない。

## テストと未確認事項

オフラインでモデル/料金の照合、思考分の費用、ヘッダーだけのキー、固定送信先、JSON形式、8ページ24コマ、承認停止、予算、並行実行、旧2.5履歴・依頼の復旧、画面の入力保持とモデル不一致拒否を検証する。
自動テストのフェイク回答は、本物の文章品質・実アカウントの適用・Googleの請求・Mac実機の成功を保証しない。今回の開発では実API0回、無料枠消費0回、API費用0円。
Macで上記のアプリ内確認を行った後、モデル、日時、HTTP/保存状態、ページ/コマの検証結果だけを記録し、キー・認証ヘッダー・送信本文・機密資料を診断ログやGitHubへ貼り付けない。
