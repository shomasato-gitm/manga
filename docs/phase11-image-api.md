# Phase 11 A：Gemini Lite画像の単発接続契約

公式資料確認日：2026-10-28。PR #26はマージ済み、両Python CIの1,418件成功を確認。最新main `2bd474e`から開発する。既存保存先・台帳・作品を初期化しない。

## 確認した契約

モデルは`gemini-3.1-flash-lite-image`、Developer API `v1beta/models/...:generateContent`の同期POSTだけ。text/inline PNG入力、1K、候補1、IMAGE、thinkingLevel=minimalまたはhigh。ツール・検索・キャッシュ・Files API・別モデル・追加送信は使わない。旧Gemini画像モデルの停止フラグは変更しない。

- [公式モデル](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite-image)：入力65,536、出力4,096トークン、1K対応、参照画像入力。完全な人物一致は保証しない。
- [Generate Content向け思考仕様](https://ai.google.dev/gemini-api/docs/generate-content/thinking)：max_output_tokensは思考を含む合計のhard cutoff。minimalは思考OFFを保証しない。上限到達でも消費した思考は課金され得る。
- [API仕様](https://ai.google.dev/api/generate-content)：candidateCount、maxOutputTokens、IMAGEモダリティ、imageConfig、usageMetadata。1候補を画像1枚の課金上限とは解釈せず、出力の全トークンを予約する。
- [Generate Content画像仕様](https://ai.google.dev/gemini-api/docs/generate-content/image-generation)：imageSize=1K、aspectRatio、inlineDataの参照。モデルページの1024px表記とRESTの1K表記を区別する。
- [公式料金](https://ai.google.dev/gemini-api/docs/pricing)：Standard入力$0.25/百万、画像出力$30/百万、テキスト・思考出力$1.50/百万。API無料枠なし。1K正方形の画像出力部分$0.0336を最大料金とは扱わない。
- [利用条件](https://ai.google.dev/gemini-api/terms)：本人が利用権・料金・送信条件を確認する。生成物の利用結果や入力素材の権利は利用者が確認。Google AI Studioや他の無料サービスをAPI無料枠と混同しない。

## 最大額

標準はmaxOutputTokens=2,048、highは4,096。入力は参照画像のトークン数を推測せず、モデルの全入力幅65,536で予約する。思考・テキスト・画像の合計を最高単価$30/百万で換算するため、追加の思考料金を取りこぼさない。

`(65536 × 0.25 + 2048 × 30) / 1,000,000 = $0.077824 / 送信`

仮の150円/USD・税1.10・安全係数1.25では1回最大16.051200円、基準2枚+24コマの26送信は最大417.331200円。テキスト費用は別。highの同前提は26送信最大746.803200円なので、500円上限で一括送信不可。標準設定・少ないページ・手動画像への切替を案内する。

これは本人が保存した為替・税・単価で再計算する。共通台帳は作品累計500円/全作品月間5,000円。アプリ側の予約はProvider側の請求上限設定とは別。公式の出力制御に反するusageが来た場合、既知のusageは精算・履歴を保持し、上限違反を記録して後続送信を停止する。

精算はinput、IMAGE、TEXT、thoughtの内訳を検証し、実usageと単価から算出する。内訳が省略された場合はIMAGEのみの応答であることを検証する。usageが不明・欠落・不一致なら0円を推測せず予約を保持する。

## 承認と復旧

既定OFF。専用フラグ`MANGA_ENABLE_GEMINI_LITE_IMAGE=1`、既存GEMINI_API_KEY、本人確認済みカード、対象本文と参照SHAに対する明示承認が必要。フラグだけでは送信できない。従来のMANGA_ENABLE_IMAGE_APIと旧アダプターの停止条件は残す。

最初の送信は1枚だけ。保存・usage・費用が確認でき、その画像の人間によるレビューを記録した後、本人が初回確認を保存するまで追加画像を送信しない。フェイクの確認はtest_onlyで、実通信の初回確認には利用不可。

その後も追加送信は、全対象・プロンプトSHA・人物/脚本/参照SHA・最大件数・最大料金・予算上限・停止条件を固定した承認が必要。変更された対象を古い承認で送らない。各リクエストは既存BudgetedExecutorの予約・実行権を1回取得し、HTTP直呼びにはその本文に対する実行権が必要。

401/403/404/429/5xx、構造化400、タイムアウト、JSON不正、モデル・usage不一致を固定分類する。キー・ヘッダー・自由文エラー・本文をログへ残さない。応答は画像・課金検証に必要な許可項目だけを既存の不変writerに保全。本文の誤形式や請求不明で自動再送しない。

既存ImageAIService/ImageAIStore、候補writer、レビュー、別操作の正式採用、画像ID、採用ジャーナルを再利用する。保存失敗後は既存の請求照合とresponse SHAから保存だけを再開する。元の台帳欠損・保存先変更では停止。料金の再確認を復旧の口実にしてAPIを再送しない。

## 開発検証と残る実機確認

実API通信0回、無料枠消費0回、API費用0円。フェイクHTTP・一時作品・一時台帳だけで検証する。Intel Mac、実アカウントの権限、実モデルの画質・人物一致・実請求は未検証。接続実装と実通信成功を分けて表示する。

PR Bで既存ホームMVPへ接続し、Macの確認は最初の1枚と追加画像の承認を含む1作品E2Eだけにまとめる。最大額・条件が未確認なら、そのE2Eでも実送信不可と表示する。
