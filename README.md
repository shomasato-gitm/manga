# AI漫画スタジオ（第1段階）

漫画の企画を入力し、パソコン上のJSONファイルに保存するStreamlitアプリです。
現在はタイトル・ジャンル・あらすじ・ページ数の作成、一覧表示、再読み込み、編集保存、JSONのダウンロードができます。
OpenAI APIへの接続、ストーリー生成、画像生成は実装していません。APIキーは不要です。

## Intel Macでの準備

推奨環境は **Intel Mac・macOS 10.15〜12・Python 3.11** です。
主要な依存パッケージを、古いIntel Mac向けのバイナリ配布があるバージョンに固定しています。
Python 3.9〜3.12に対応する構成ですが、まずは3.11を利用してください。
macOS 10.14以前はPyArrowの配布対象外のため、この構成では対応しません。
Mac実機での動作確認は未実施です。

1. Pythonがなければ [Python公式のmacOSダウンロード](https://www.python.org/downloads/macos/) から、macOSの対応条件を確認してPython 3.11をインストールします。OS付属のPythonは変更しません。
2. ターミナルを開き、ダウンロードまたはcloneした `manga` フォルダに移動します。
3. 以下を1行ずつ実行します。`.venv` は、このアプリ専用のPython環境です。

```bash
cd /path/to/manga
python3.11 --version
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address 127.0.0.1
```

`/path/to/manga` は実際のフォルダのパスに置き換えてください。
ブラウザが自動で開かなければ、ターミナルに表示されるLocal URLをブラウザで開きます。
終了するにはターミナルで `Control + C` を押します。
次回からはフォルダに移動し、`source .venv/bin/activate` と起動コマンドだけで起動できます。

Pythonのコマンドが見つからない場合はインストール後にターミナルを開き直し、`python3.11 --version` を確認してください。
証明書エラーが出る場合、python.orgのインストーラーに付属する `Install Certificates.command` を実行します。証明書検証を無効にする必要はありません。
別のアプリがポートを使用している場合は、起動コマンドの末尾に `--server.port 8502` を追加できます。

## 使い方

1. タイトル・ジャンル・あらすじを入力します。ページ数は初期値8、指定範囲は1〜100です。
2. 「プロジェクトを保存」でJSONファイルが作成されます。
3. 左側の一覧から選び、「選択したプロジェクトを開く」で再読み込みします。
4. 読み込んだ内容を変更して保存すると、同じプロジェクトを更新します。
5. 「新規プロジェクト」でフォームを空にすると、別のプロジェクトを作成できます。同じタイトルでも別々のIDで管理します。

一覧の「開く」や「新規プロジェクト」は未保存の入力を置き換えます。必要な内容は先に保存してください。
JSONのダウンロードは最後に保存した内容が対象です。フォームの未保存内容は含みません。
この版は個人がローカルで使う想定です。同じプロジェクトを複数の画面で同時に編集すると、最後の保存が反映されます。

## 保存先とデータ

標準の保存先はリポジトリ内の `data/projects/<UUID>.json` です。
JSONはUTF-8で日本語をそのまま保存し、タイトルをファイル名に使わないため、同名の企画も保存できます。
一時ファイルに書いてから置き換え、保存途中の失敗で既存のJSONが壊れることを防ぎます。
壊れたJSONは一覧に警告を表示し、正常なファイルは引き続き読み込みます。

`data/projects/` 内のJSONはGit管理対象外です。バックアップする場合はこのフォルダを別の場所にコピーしてください。
保存先を変更するときは、起動前に環境変数を設定します。相対パスは起動したフォルダが基準になるので、絶対パスを推奨します。

```bash
export MANGA_DATA_DIR="$HOME/Documents/manga-projects"
python -m streamlit run app.py --server.address 127.0.0.1
```

主なJSON項目は次のとおりです。

| 項目 | 内容 |
| --- | --- |
| `schema_version` | JSON形式のバージョン（現在は1） |
| `id` | プロジェクトを識別するUUID |
| `title`, `genre`, `synopsis`, `page_count` | 入力した企画 |
| `created_at`, `updated_at` | UTC・タイムゾーン付きの作成／更新日時 |
| `workflow` | 将来の工程・完了済み工程・成果物パスの保存領域 |
| `budget` | 将来の上限金額・累積費用の保存領域（米ドルの10進数文字列） |

現在、`workflow` と `budget` は保存・再読み込みするだけです。
処理の途中再開や費用上限チェックは未実装で、今後サービス層に追加します。
未対応の `schema_version` は読み込み時に警告となります。将来形式を変える際は、データ移行処理を追加する想定です。

## ディレクトリ構成

```text
manga/
├── app.py                  # Streamlitの画面
├── manga/
│   ├── models.py           # プロジェクトの定義と入力チェック
│   ├── storage.py          # JSONの保存・一覧・読み込み
│   └── services/README.md  # 今後の生成・評価・出力処理の追加先
├── data/projects/          # 利用者のJSON（Gitには含めない）
├── tests/                  # データと画面の自動テスト
├── requirements.txt        # 実行に必要なパッケージ
├── requirements-dev.txt    # テストを含む開発用パッケージ
└── pytest.ini              # テスト設定
```

将来のAPIキーはソースやJSONに書かず、環境変数などから読み込みます。
`.env` と `.streamlit/secrets.toml` はGit管理対象外です。現段階では読み込み処理も不要なため実装していません。
Pillowは将来の画像処理用に導入していますが、画像生成・編集コードはまだありません。

## テスト

仮想環境を有効にした状態で実行します。

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

テストは一時フォルダを使うため、実際の保存データを書き換えません。
JSONの日本語保存・読み込み、更新、同名プロジェクト、入力エラー、不正なJSON、保存失敗時の保護を確認します。
StreamlitのAppTestでは、入力→保存→編集→新しいセッションでの再読み込みまで確認します。
テストにもAPIキーや外部APIへの接続は必要ありません。

今回の確認環境はLinux・Python 3.12です。30件の自動テスト、依存関係の整合性チェック、
Streamlitサーバーの起動とHTTP応答を確認しました。
加えて、Python 3.11・macOS 10.15・Intel向けの依存パッケージ一式を
ソースビルドなしで取得できることを確認しています（Macでの実行確認ではありません）。
