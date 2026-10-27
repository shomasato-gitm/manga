# Gemini APIキー読込の通信なし診断

確認日：2026-10-27（日本時間）。Macの直接接続HTTP 200/OKとスタジオの400/API_KEY_INVALIDはユーザー報告です。CodexはMacのキー・起動環境・成功時のコマンドを取得しておらず、今回の原因は確定していません。実Gemini API送信は行いません。

## 確認できた経路

| 対象 | 読み取り・上書きの仕様 |
|---|---|
| `manga/gemini_credentials.py` | テキスト認証の共通読込。`os.environ['GEMINI_API_KEY']`のみ。従来の形式検証を維持し、空白除去・引用符除去・別キーへの切替はしない。 |
| `manga/story_ai_gemini.py` | 共通`api_key`を互換名で公開。通常の企画・脚本・評価・改善の準備と送信時に使用。明示注入の`key`は既存テスト用の境界として残す。通常UIは注入しない。 |
| `manga/gemini_diagnostics.py` | 固定文診断の事前確認・送信も同じ読込関数。キーを料金JSON・履歴・予算承認SHAへ入れない。 |
| `manga/story_ai_ui.py` | 生成方式選択時のキーの有無表示は`GEMINI_API_KEY`の存在のみ。認証成功の表示ではない。 |
| `manga/api_architecture_diagnostics.py` | 環境変数の有無だけを診断。ネットワークなし。 |
| `manga/image_ai_live_provider.py`, `manga/image_ai_readiness.py` | 画像用も同名環境変数を読む。今回変更なし。本番画像送信停止を維持。 |
| `manga/github_sync_ui.py::github_token` | 環境変数にGitHubトークンがなければ`st.secrets`を読む。同期処理・認証仕様は変更しない。 |
| Streamlit 1.40.2 | 起動時`web/bootstrap.py::_on_server_start`からSecretsを読み、`runtime/secrets.py::_parse`がトップレベルの文字列・数値を`os.environ`へ設定する。既存値も上書きする。Secrets再読込時も影響し得る。 |
| `.env` / `GOOGLE_API_KEY` | アプリにdotenvローダーやGoogle SDKの自動認証探索はない。起動スクリプト・IDE・direnv等が事前に読み込んだ場合、最終的なプロセス環境の由来までは復元できない。 |

コード・設定・依存宣言・起動スクリプトを`GEMINI_API_KEY / GOOGLE_API_KEY / dotenv / load_dotenv / st.secrets / os.environ`で調査しました。実際の秘密ファイルや環境変数の値は表示・取得していません。

[Google公式のAPIキー案内](https://ai.google.dev/gemini-api/docs/api-key)では、公式クライアントライブラリに両方の変数が設定されている場合、`GOOGLE_API_KEY`が優先されます。スタジオはSDKではなく明示的なHTTP処理で`GEMINI_API_KEY`だけを使います。直接テストがSDKを使ったかは未確認です。

[Streamlit公式Secrets説明](https://docs.streamlit.io/develop/concepts/connections/secrets-management)では、トップレベル値が環境変数にもなることと、プロジェクト側設定がグローバル設定より優先されることが示されています。`~/.streamlit/secrets.toml`、作業ディレクトリの`.streamlit/secrets.toml`、カスタム`secrets.files`は上書き要因の候補です。今回のMacに存在するとは断定しません。

## 直接接続とスタジオのリクエスト比較

| 項目 | 成功した直接接続 | スタジオの3.5固定文診断 |
|---|---|---|
| API・モデル | ユーザー報告：v1beta / generateContent / gemini-3.5-flash-lite | 同じ固定HTTPSエンドポイントへのPOST |
| 認証 | 変数名・明示キー・送信方法は未取得 | 現在のプロセスの`GEMINI_API_KEY`を`x-goog-api-key`ヘッダーへ設定。URL・本文には入れない |
| 本文 | OKを返したとの報告。正確なJSONは未取得 | user/parts/textの固定文、candidateCount=1、maxOutputTokens=256、thinkingLevel=minimal |
| 構造化出力 | 未確認 | 疎通診断では指定なし。通常生成は既存の`responseFormat.text`スキーマを使用 |
| SSL | certifi設定済みとの報告 | Python標準のHTTPS検証。TLS検証を無効にせず既存の`SSL_CERT_FILE`設定を引き継ぐ |

[公式generateContent仕様](https://ai.google.dev/api/generate-content)、[3.5モデル](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)、[思考設定](https://ai.google.dev/gemini-api/docs/generate-content/thinking)と照合しました。今回、3.5の診断本文を変更する根拠は見つかっていません。400の構造化`google.rpc.ErrorInfo / domain=googleapis.com / reason=API_KEY_INVALID`は認証の分類であり、どこで値が異なったかや、思考設定・スキーマの誤りを証明しません。自由文のエラーだけから原因を推測しません。

## Macでの確認（API通信0回）

1. 現在の作品・共有予算台帳の保存先を維持してバックアップします。既存の未確定診断は[請求照合手順](gemini-diagnostic-recovery.md)に従い、Googleの請求確認なしに解除しません。今回の確認には台帳操作も不要です。
2. 直接接続に成功したターミナルを使用し、スタジオのサーバーをCtrl+Cで停止します。ブラウザ更新だけでは親プロセスの環境変数は更新されません。複数のスタジオや異なるポートを開いていないか確認します。
3. 既存のPython仮想環境を有効にし、リポジトリのルートから次を実行します。

   ```bash
   python -m manga.gemini_credentials
   ```

   出力は設定の有無、形式の注意点、2つの変数の一致/不一致等だけです。値・先頭末尾・長さの実数・ハッシュは出ません。ここでの起動時一致はこの短いプロセス内だけの比較で、過去の直接接続との一致やキーの有効性を保証しません。

4. `key_set=false`なら、成功した直接テストが未exportのシェル変数を使っていないか確認します。既存の変数をPythonへ渡す操作は`export GEMINI_API_KEY`です（値をコマンドへ書かない）。`google_comparison=different`なら、成功したコマンドがどちらを使ったか、手元でソースを確認します。公式SDKの優先順位とスタジオの違いに注意します。正しい設定元は本人が判断し、アプリはキーをコピー・選択しません。
5. Streamlitによる上書きを切り分ける場合は、同じターミナルから任意の調査用起動を使います。

   ```bash
   python -m manga.gemini_startup
   ```

   Streamlitを起動する前に比較基準をメモリ内だけに置き、同じプロセスで従来のCLIを起動します。既存の環境・作品・台帳の保存先は変更しません。従来の`--server.port=8501`等も末尾に渡せます。通常の`python -m streamlit run app.py`も引き続き使えます。

6. 「企画・脚本 → AI企画・ストーリー → 制作条件 → Geminiの接続・無料枠・料金設定 → 初回接続診断」の「キーの読み込み状態を確認する（通信なし）」を押します。料金JSONやモデル権限の確認前でも使えます。

   - 基準が**Streamlit起動前**で「不一致」：起動後の環境変更を検出。Secrets、起動フック等を手元で確認します。変更元そのものは断定できません。
   - 基準が**アプリ初回実行時**で「一致」：Secretsによる起動前の上書きは判定できません。調査用起動に切り替えます。
   - 2つの環境変数が「不一致」：直接テストの認証元を確認します。
   - 空白、引用符、export文などの注意：安全な設定元で確認します。自動補正しません。文字種だけでGoogleの認証可否は判定しません。

7. Secretsを調べる場合はローカルの安全なエディターで確認し、値をチャット、ログ、JSON、スクリーンショット、Gitへ貼りません。`cat .env`、`env`、`printenv`、`set -x`、`curl -v`等による値の出力は使用しません。JSONにキーを保存する必要はありません。診断ツールは秘密ファイルを探索・読み取りません。

比較基準はアプリプロセスのメモリだけに保持し、再起動で消えます。複数ブラウザや作品の切替で再設定しません。共有プロセスの比較結果であり、ユーザー別のキー保管庫ではありません。通常生成の準備・診断の事前確認・実送信は同じ読込関数ですが、その間に環境が外部から変更されれば読込値も変わり得ます。この比較は承認を特定のキーや過去の通信にひも付けたという意味ではありません。

## 維持する安全境界

認証経路・モデル・単価・本文の自動変更は行いません。調査パネルとCLIは通信・予算予約・作品保存をしません。アプリの初回捕捉も読取とメモリ保持だけです。比較用オブジェクトはreprで値を出さず、シリアライズを拒否します。

実通信は従来どおり、利用条件・無料枠・送信内容・最大料金の確認、個別承認、予算予約、1回の実行権が必要です。作品累計500円・全作品月間5,000円、請求不明時の予約保持・停止・再送禁止を維持します。400/API_KEY_INVALIDでも既存予約を自動解除しません。今回は再試験を実施せず、必要なら照合完了後に本人が既存の実通信手順を別途承認します。

旧2.5のデータ、候補・採用、無料モック、手動JSON、音声、画像、GitHub同期の仕様は変更しません。Storeや台帳のスキーマ変更はありません。

## 自動テストと未確認事項

テストはフェイクのキーとHTTPだけを使います。環境変数の不一致、Secretsの上書き、rerun後の基準保持、未export相当の未設定、.env非読込、形式の注意、CLI出力、3.5の実HTTPコードのヘッダー・本文、400分類・予約保持・再送禁止、AppTestでの画面操作を検証します。

Mac上の実際の原因、どのキーが直接成功したか、Google側の権限・請求、アプリ内の実通信は未確認です。オフラインテストの成功を実API成功とは扱いません。
