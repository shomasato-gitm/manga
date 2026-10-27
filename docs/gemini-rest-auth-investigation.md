# Gemini 3.5認証エラー：REST形式と送信時点の調査

確認日：2026-10-27（日本時間）。結論：**Macで発生した400/API_KEY_INVALIDの原因は未確定**です。原因を裏付けられないため、アプリのAPIキー取得、モデル、単価、HTTP送信仕様、診断本文、エラー分類は変更しません。今回の変更はオフライン回帰テストと調査資料です。

## 証拠の区分

ユーザーからの報告：

- Macの直接REST接続は`gemini-3.5-flash-lite / v1beta / generateContent`でHTTP 200、OK。
- 成功した直接接続はPython 3.11の`urllib.request`によるHTTP POST。環境変数`GEMINI_API_KEY`を`x-goog-api-key`へ設定し、URLのkeyは未使用。generationConfigは`maxOutputTokens: 64`のみ。
- スタジオはHTTP 400、構造化エラーの分類はAPI_KEY_INVALID。
- 通信なし比較はGEMINI_API_KEY設定あり、Streamlit起動前後で一致、GOOGLE_API_KEYなし、基準はStreamlit起動前。

この報告では、比較時点での上書きは検出されていません。上書きが起きたと仮定しません。キーの変数名・認証の置き場所は一致していますが、直接接続に成功したキーと診断時のキーの実際の値が同一だったかは未確認です。成功時のcontents、追加ヘッダー、Content-Type、キー取得の時点、タイムアウトも未確認です。

開発環境で確認した事実：

- 送信直前に取得したフェイクキーが、`urllib.request`と`http.client`のHTTP組立を通って、認証ヘッダーへ1つだけ、変換されずに渡る。
- 成功時のメソッド・認証・generationConfigを本人報告に合わせたフェイク参照とスタジオの診断を、同じ固定contentsで比較した。既知の生成設定差だけを検出し、認証ヘッダーの脱落・変換は検出しなかった。実キー・実本文を復元したものではない。
- スタジオの診断に、URLクエリ認証、Authorization、JSON構造化出力の指定はない。キーは本文に入らない。
- Google公式のGenerateContentエラー例と現在のAPI_KEY_INVALID分類条件が一致する。自由文に同じ語があるだけでは分類しない。
- 実Gemini API送信0回。フェイクの200/OKは実モデルの動作確認ではない。

## リクエスト比較

| 項目 | 本人報告の成功形式を使ったフェイク参照 | スタジオの初回診断 | 成功時の直接接続との照合 |
|---|---|---|---|
| URL | `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent` | 同じ固定URL | API版・モデル・操作名は報告と一致。keyクエリなし |
| HTTPメソッド | POST | POST | 本人報告と一致 |
| 認証 | GEMINI_API_KEYから`x-goog-api-key`（フェイクキーのみ） | GEMINI_API_KEYから`x-goog-api-key`のみ | 変数名・ヘッダー名は一致。過去のキーの値の同一性は未確認 |
| Content-Type | application/json | application/json | 未取得 |
| contents | 差を切り分けるためスタジオと同じ固定文を仮に使用 | user/parts/text、固定のOK要求文 | 成功時のcontentsは未取得。本人の実本文を再現したとは扱わない |
| generationConfig | maxOutputTokens=64のみ | candidateCount=1、maxOutputTokens=256、thinkingConfig.thinkingLevel=minimal | この差は本人報告から確認済み。これが認証エラーの原因という証拠はない |
| JSON構造化出力 | 指定なし | 指定なし | 未取得。通常の企画/脚本とは別の疎通診断 |
| 接続・各ソケット操作のタイムアウト | 比較テストは30秒 | `open(timeout=30)` | 成功時の設定は未確認 |
| 本文受信の期限 | 比較参照では指定なし | 受信開始後にmonotonicで120秒を検査 | 厳密な全体120秒上限ではない。各readは30秒待つ場合がある |
| リダイレクト | テストではNoRedirect | NoRedirect | 成功時の追従設定は未確認 |
| HTTPライブラリ | Python標準urllib（テストは3.11/3.12で実行） | Python標準urllib。通常はPython版等のヘッダーも付く | 成功時もPython 3.11のurllibとの本人報告。Macの実行プロセス・追加設定の差は未確認 |

参照リクエストは本人報告のメソッド・認証方式・生成設定を使ってフェイク用に作ったもので、ユーザーの成功コマンド全体やキーを復元したものではありません。追加で、generationConfigを省略した公式の最小構造とkeyクエリ認証も、オフラインだけで比較します。クエリ認証は今回成功した本人の方式ではありません。

省略と明示設定の差は、必ずしも動作の差ではありません。candidateCountの省略時は1で、3.5の思考既定もminimalです。出力上限64と256の差はありますが、API_KEY_INVALIDと結び付ける根拠はありません。これらの参照は承認済み実診断とは別のオフライン比較専用です。実診断を参照形式に置き換えたり、上限を外したりしません。

JSONの空白、辞書のキー順序、HTTPヘッダー名の大文字小文字は、今回のオフライン比較では意味の違いとして扱いません。実際のMac上のHTTP/TLSやプロキシによる挙動まで検証したという意味ではありません。

## キーを読む時点

1. `manga/gemini_credentials.py::capture_startup`：比較用基準をメモリだけに保持。送信用キーのキャッシュではない。
2. `credential_report` / `offline_report`：その時点の環境を読む。過去の成功キーとの一致や、未来の送信キーを保証しない。
3. `manga/gemini_diagnostics.py::run`：予算予約・実行前に`api_key()`で形式確認する。
4. `Probe.generate`：共通予算の実行権を取得後、`api_key()`を再度呼び、結果を`transport(body, key)`へ渡す。
5. `manga/story_ai_gemini.py::transport`：渡されたkeyをRequestの`x-goog-api-key`に設定する。ここでは環境を読み直したり、引用符除去・strip・別キーへの切替をしたりしない。
6. `urllib`はRequestのヘッダーをHTTP送信用に組み立てる。新テストではこの段階まで通して検証する。

通常の企画・脚本生成は`GeminiStoryAI.prepare`と`generate`から同じ`api_key`を使用します。UIから明示キーを注入する経路はありません。テスト用の`key`注入境界は既存仕様です。

テストでは、事前比較の後に環境を仮に変更すると送信直前の値が使われることも再現します。これは時点の限界を示すテストで、今回のMacで実際に変更が起きた証拠ではありません。アプリの認証方式を推測で修正する理由にはしません。

## 公式API仕様の確認

| 設定・分類 | 根拠と結論 |
|---|---|
| v1beta / POST / contents | [generateContent REST](https://ai.google.dev/api/generate-content)のエンドポイント・リクエスト構造に対応 |
| x-goog-api-key / key | [Googleのシステムパラメーター](https://docs.cloud.google.com/apis/docs/system-parameters)で両者を定義。スタジオはヘッダーのまま維持 |
| thinkingLevel=minimal | [公式思考設定](https://ai.google.dev/gemini-api/docs/generate-content/thinking)の表で3.5 Flash-Lite対応・既定。完全な思考OFFではない |
| candidateCount=1 | [GenerationConfig](https://ai.google.dev/api/generate-content#GenerationConfig)に正式項目として定義。省略時も1 |
| maxOutputTokens=256 | 同じGenerationConfigの正式な整数項目。[3.5の公開出力上限65,536](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)以内。実アカウントでの受理は未検証。短い上限で出力が途切れる可能性と認証エラーは区別する |
| 400/API_KEY_INVALID | [GenerateContentのエラー形式](https://ai.google.dev/gemini-api/docs/generate-content/api-errors)に、code=400、status=INVALID_ARGUMENT、details内のErrorInfo / domain=googleapis.com / reason=API_KEY_INVALIDという例が明記されている |

`/gemini-api/docs/api-errors`はInteractions API向けの説明を含むため、今回の根拠にはGenerateContent専用ページを使用しました。公式のリトライ推奨やモデル切替の記載は、本アプリの個別承認・再送禁止を解除する許可とは扱いません。

現在の分類関数は、本文を最大16KiB+1までメモリ内で読み、固定の構造化項目だけから分類し、本文・message・metadata・ヘッダーを保存しません。分類が「APIキー無効」でも、誤入力、別キー、発行元、失効、通信経路等のどれが原因かはこの情報だけで特定できません。thinkingConfigを指すBadRequestは別分類です。

## Macで次に確認する手順

### 1. 実通信をせず、同じコードのHTTP組立を確認

PRのブランチへ切り替え、既存の仮想環境を使用します。開発用テスト依存が未導入なら`python -m pip install -r requirements-dev.txt`で導入します（この依存導入はパッケージ取得であり、AIへの送信ではありません）。

```bash
python --version
git rev-parse --short HEAD
python -m pytest -q -s tests/test_gemini_rest_wire.py
```

このテストは実際のキーを使いません。フェイクキー・一時テスト作品・一時予算台帳を使い、DNS/TCP接続を禁止してHTTPSConnectionをメモリ内の接続へ置き換えます。TLS通信もありません。実際の作品・共有台帳を読み書きしません。

表示する比較結果は、POSTか、パスが一致するか、認証ヘッダーの個数、クエリ認証の有無、生成設定の一致などの固定項目だけです。キー、キーの一部・ハッシュ、認証値、URL全文、本文は表示しません。`key_identity_with_past_success=unverified`は、過去の直接成功との同一性を確認していないという意味です。

### 2. まだ分かっていない成功時の仕様を確認

手元で成功時のコードを確認し、まだ未確認のcontents、Content-Type、追加ヘッダーの有無、キー取得の時点、タイムアウトを上の表と比較します。今回報告済みのurllib/POST/x-goog-api-key/maxOutputTokens=64を確認し直す必要はありません。コード全文・ヘッダー値・キーを共有する必要もありません。HTTPデバッグ、環境変数の一括出力は使いません。

### 3. 必要なら既知の成功キーとの一致をメモリ内だけで確認

成功時に使ったキーを安全な設定元から本人が特定できる場合だけ、成功したターミナルで次を実行できます。隠し入力と環境の比較だけで、通信・ファイル保存・予算予約はしません。値を入力コマンドの引数へ書く必要はありません。TTYなし・隠し入力不可の場合は停止します。

```bash
python -c '
import getpass, hmac, sys, warnings
from manga.gemini_credentials import api_key
if not (sys.stdin.isatty() and sys.stderr.isatty()):
    raise SystemExit("対話ターミナルで実行してください。比較は未実施です。")
warnings.simplefilter("error", getpass.GetPassWarning)
known = getpass.getpass("直接成功時と同じ設定元のキー（非表示入力）: ")
current = api_key()
print("一致" if hmac.compare_digest(known.encode(), current.encode()) else "不一致")
'
```

これは本人が入力した値と**現在のターミナル環境**の比較です。入力元を取り違えた場合や過去のキーが不明な場合、過去の成功との一致の証明にはなりません。Googleへの認証や無料枠も検証しません。

続けて同じターミナルで`python -m manga.gemini_startup`からスタジオを起動し、既存の「キーの読み込み状態を確認する（通信なし）」で起動前後の一致を確認します。すでに一致を確認した今回の報告と、過去の直接成功キーの同一性は別の確認項目です。

### 4. HTTPクライアントの環境差は値を出さずに確認

今回の成功時もスタジオもurllibですが、別プロセスなら環境変数、macOSのプロキシ、TLS証明書設定が異なる可能性があります。必要なら各起動環境でPythonがプロキシ設定を認識しているかだけ確認できます。

```bash
python -c 'from urllib.request import getproxies; p=getproxies(); print({k: bool(p.get(k)) for k in ("http","https","all","no")})'
```

URL・プロキシ認証情報は出力しません。設定ありというだけで、プロキシが原因とは判定しません。プロキシ迂回、SSL検証無効化、APIキーをクエリへ移す変更は行いません。Macの実TLS/ネットワーク挙動は今回未検証です。

### 5. 請求未確定分は従来の手順で照合

既存の[請求復旧手順](gemini-diagnostic-recovery.md)で、対象リクエストとGoogle側の請求を本人が照合します。400でも、請求額が不明なまま自動解除しません。今回は新しい実通信手順や再送ボタンを追加しません。再テストが必要になった場合も、照合完了・料金/無料枠/送信条件の確認後、アプリ既存の最大額表示と個別承認を使います。

## 安全性と残る確認

作品500円・全作品月間5,000円、個別承認、予算予約、請求照合、実行権の1回制限、結果不明時の予約保持は既存実装をそのまま使用します。既存作品・履歴・脚本・音声・画像・採用・請求未確定記録は変更しません。料金・モデル・保存スキーマの変更はありません。

既存の400履歴を表示している場合は、対象リクエストIDと日時も確認してください。通信なし比較をした後に表示された履歴でも、それより前のキー・リクエストでの結果かもしれません。新規通信があったと推測せず、再送もしません。

残る確認は、成功時のcontents等の未提供の非機密仕様、成功時と診断時のキー同一性、Macの実際のHTTP/TLS経路、Google側の権限・請求です。オフラインで組立が正しいことと、Googleが実際のキーを認証することは分けて報告します。
