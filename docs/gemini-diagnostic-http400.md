# Gemini初回診断のHTTP 400調査と修正

確認日：2026-10-27（日本時間）。Codexによる実Gemini API送信・無料枠消費・API費用は0。
**HTTP 400だけでは、報告された診断の正確な原因は特定できない。**
旧版はエラー本文を保存しておらず、今回の修正で過去の原因を復元・再分類しない。
利用者のMacの記録・請求は操作していない。実通信・当該アカウントの権限・Mac実機は未検証。

## 公式仕様との照合

`ai.google.dev`のガイド本文は開発環境の取得制限（HTTPプロキシの403）により取得できなかった。
これは利用者のGemini APIの400とは別の問題である。代わりにGoogle公式リポジトリの公開API定義・SDK・Cookbookを読み取った。以下のソースを実行してはいない。

|資料|今回確認した内容|取得したblob SHA|
|---|---|---|
|[Generative Language v1beta公式定義](https://github.com/googleapis/googleapis/blob/master/google/ai/generativelanguage/v1beta/generative_service.proto)|`/v1beta/models/*:generateContent`、GenerationConfig、ThinkingConfig、JSON Schemaの対応項目|`6c3ff50b92d32f5c3d3eeef73a736761d5fa1990`|
|[Google Gen AI Python SDK](https://github.com/googleapis/python-genai/blob/main/README.md)|`response_mime_type=application/json`と`response_json_schema`の併用例|`6cca3cd0a9881319c2f3d004d82951e896fd22dc`|
|[公式Thinking REST例・固定した過去版](https://github.com/google-gemini/cookbook/blob/d4bf2113234550cfeeccc9f1f96180e234fd1782/quickstarts/Get_started_thinking_REST.ipynb)|2.5 Flash-Liteを選択肢に含み、非Proモデルで`generationConfig.thinkingConfig.thinkingBudget=0`を示す。2025年の例であり、現在のアカウント権限の証明ではない|`3a5f0020a6c4a7e63863a11047757b8f24084034`|
|[Google RPCエラー定義](https://github.com/googleapis/googleapis/blob/master/google/rpc/error_details.proto)|ErrorInfo、BadRequest.fieldViolations.fieldと自由文descriptionの分離|`43b7d04d2baa116ed0f943322bec1db65af66e55`|
|[Google API ErrorReason](https://github.com/googleapis/googleapis/blob/master/google/api/error_reason.proto)|`API_KEY_INVALID`、`domain=googleapis.com`の構造化エラー例|`3b4826e5f74739bf642ac99d289dd942770035eb`|

現在のモデル提供・モデル固有の条件を公式サイト本体で再確認できたとは扱わない。モデルや料金をこの調査で更新しない。
元の[Phase 9-B2確認記録](phase9b2-text-ai.md)と本人による料金・権限・規約確認を引き続き必要とする。

|調査対象|既存リクエストと検証結果|
|---|---|
|URL/方式|固定HTTPSのv1beta `generateContent`へPOST。APIキーはヘッダー。API定義と一致|
|contents|`role=user`、`parts[].text`。基本構造に明白な誤りは見つからない|
|generationConfig|`candidateCount=1`、`maxOutputTokens=256`。公式定義にある項目|
|thinkingConfig|generationConfigの内側にあり、thinkingBudgetは整数0。配置を誤っているとは判断できない。対応しないモデルではエラーになる旨が公式定義にあるが、今回の400がこれによるとは断定しない|
|responseJsonSchema|公式定義・SDKに存在する。`responseSchema`との併用不可、responseMimeType必須という条件にも既存JSONは適合|
|additionalProperties=false|公式API定義の対応項目にadditionalPropertiesがある。これだけを原因と断定しない|
|利用条件・認証|400が常にスキーマ不正とは限らない。構造化statusがFAILED_PRECONDITION、またはErrorInfoがAPI_KEY_INVALIDの場合も区別する。既存の一般的な400分類から遡って推定しない|

**結論：公式定義だけから旧JSONの特定項目を400の確定原因とはできない。**
初回の疎通確認がJSON構造化出力の検証も兼ねていたため、不要なオプションを分離し、次の診断で原因候補を絞れる構成にした。

## 修正後の固定文診断

送る本文は従来どおり固定文だけ。作品・人物・画像・音声・通常脚本のsystemInstructionは含めない。

```json
{
  "contents": [{"role": "user", "parts": [{"text": "Connection diagnostic only. Return exactly this JSON: {\"ok\":true}"}]}],
  "generationConfig": {
    "candidateCount": 1,
    "maxOutputTokens": 256,
    "thinkingConfig": {"thinkingBudget": 0}
  }
}
```

診断からresponseJsonSchema、responseSchema、responseMimeTypeを除外した。モデルの既定のテキスト出力を使い、返されたJSONテキストをアプリ内で厳密に確認する。
形式違いは成功扱いにせず、通常生成へフォールバックして再送もしない。
思考OFFを維持するためthinkingConfigは残す。これを外して既定の思考動作へ変えることはしない。
成功は固定文への有効応答・モデル・usageの確認だけを意味し、JSON構造化出力機能、脚本品質、無料枠や公開可否を保証しない。

通常の企画・脚本・評価・改善は従来どおりresponseMimeType=application/jsonとresponseJsonSchemaを使い、受信後のスキーマ/ページ/コマ/人物検証と人間の採用を維持する。
最小診断への切替を通常の生成へ適用しない。モデル`gemini-2.5-flash-lite`、料金JSON、入力幅、出力上限、1回の実行権、機能フラグは変更しない。
本文のSHAが変わるため、旧リクエストへの承認を新しい送信には再利用できない。旧履歴・予約・保存済み応答の照合は引き続き可能。

## 新しい400の分類

既に受け取ったHTTP 400本文をメモリ上で最大16KiB+超過検知1byteだけ読み、固定分類へ変換する。
エラーのmessage、description、metadata、URL、キー、ヘッダー、レスポンス本文をログ・履歴・画面・作品JSONへ渡さない。
本文の自由文に含まれる単語から原因を推測しない。読込失敗・超過・不正JSON・未知の構造なら従来の一般的な400に戻す。

|構造化された証拠|表示する分類|
|---|---|
|status=FAILED_PRECONDITION|実行前提条件。地域・利用条件・請求設定等のどれかは断定しない|
|status=INVALID_ARGUMENTと公式ErrorInfo.API_KEY_INVALID|キー無効。キーの値は表示しない|
|公式BadRequest.fieldViolations.field|JSONスキーマ、思考設定、MIME、出力上限、contents、生成設定全体、複数項目の固定分類|
|INVALID_ARGUMENTだが認識できる項目がない|不正な引数・対象項目不明|
|安全な証拠がない|従来の400・詳細分類不可|

認識した項目名そのものも保存せず、固定enumだけを段階ログへ残す。未知の項目・値を反射表示しない。
通常生成の失敗時の停止・復旧契約は維持する。

## 請求・安全性

HTTP 400でも予約を自動精算・解除しない。作品累計500円/全作品月間5,000円を維持する。
既存の請求未確定記録を削除・初期化・再分類しない。本文を簡略化しても、最大入力幅1,048,576/出力256 tokensによる保守的な正の予約額を小さく変更しない。
無料枠が不明なら0円と推測しない。個別承認、共有予算予約、固定本文SHA制約、二重送信防止、自動リトライ禁止をそのまま使用する。
保存・請求照合ボタンはAPIを再送しない。通常生成、OpenAI、音声、画像、同期、自動進行の条件を変更しない。

## Macでの再確認

1. アプリを停止し、既存データと共有予算台帳をバックアップする。`MANGA_DATA_DIR`・`MANGA_BUDGET_DB`を変更しない。
2. 既存の400診断が請求未確定なら、[既存の請求照合手順](gemini-diagnostic-recovery.md)でGoogle側の確定結果を本人が確認する。「データなし」や400だけで無課金にしない。不明なら予約を維持し、ここで止める。
3. このブランチで既存の仮想環境から起動する。

   ```sh
   git fetch origin
   git switch feature/gemini-diagnostic-http400
   python -m streamlit run app.py
   ```

4. 「企画・脚本 → AI企画・ストーリー → 制作条件 → Geminiの接続・無料枠・料金設定 → 初回接続診断」で、固定文診断が構造化出力を検査しないという案内を確認する。ここまでAPI送信不要。
5. 新しい実診断を行う場合だけ、本人が公式提供状況・利用条件・料金・無料枠と残予算を確認し、既存のモード/機能フラグ、固定文と最大額を確認して新たに1回だけ承認する。旧承認を流用しない。
6. HTTP・検証・保存・請求の各状態を確認する。400が再発しても連打やモデル切替はせず、表示された固定分類とGoogle側の設定・請求を照合する。過去の一般的な400が詳細分類へ自動更新されることはない。

開発・CIでは上の実診断を行わず、フェイクHTTPと専用tmp作品で検証する。実際の400の解消はMac上での明示した単発確認まで未検証。
