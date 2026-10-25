# Phase 9-B1：日本語音声（VOICEVOX / 手動WAV）

PR #16のmainマージ（47806db）を確認し、`feature/phase9b1-japanese-voice`で実装。
既存のVoiceService / VoiceStore / WAV検証 / 音声ジョブ・staging・復旧 / 字幕 / FFmpeg動画を使用する。
有料APIの実行、VOICEVOXの実接続、ソフトのダウンロード・インストールは開発中に行っていない。

## 利用できる方式と操作

「音声・動画 → 音声を作る」（互換ルート：完成・書き出し → 音声制作・字幕）の「準備・話者設定」で人物ごとに選ぶ。

|方式|生成・取り込み|利用前の条件|
|---|---|---|
|WAV手動取り込み|録音や利用許諾を得た日本語WAVを取り込む|FFmpeg、対象確認票、出典・利用条件の記録|
|VOICEVOX|このアプリを起動したMacのエンジンで1発話ずつ合成|ローカル接続を明示有効化、接続操作、話者対応、各規約確認|
|無料モック|既存の確認用トーン|日本語を読み上げる人声ではない。完成品質としない|
|Amazon Polly|従来の有料経路を保持|既定OFF。料金・上限・共有予算・個別承認が必要。本変更では有効化しない|

1. 人物別に生成方式・声を保存する。漫画の人物UUIDとVOICEVOX話者UUID/スタイルIDは区別して記録する。
2. 「音声生成・試聴」で発話を選び、話者・読みを確認する。読みだけを編集しても字幕の脚本原文は保持する。
3. WAV方式ではその発話の「音声の対象確認票」を取得し、WAVと確認票JSONを同じ画面で選ぶ。
4. 出典、ソフト利用条件、音声ライブラリ/録音許諾、必要なクレジットを入力する。ソフトと音声固有の規約確認は別チェック。
5. WAVを候補保存、または1発話のVOICEVOXプレビューを明示生成。未確認WAVは候補として残せるが正式割り当てできない。
6. 候補を切り替えて試聴し「音声の試聴確認を記録」。続けて別ボタンの「この発話へ正式割り当て」。画像の承認は流用しない。
7. 全発話を用意し「字幕・タイムライン」で新しい版を保存。WAVの実測フレーム数から字幕開始・終了時刻を計算する。
8. 動画工程でこのタイムラインを選び、YouTube本編・TikTok・Shortsの編集と素材権利を確認してMP4を出力する。

候補の保存だけでは現在の割り当ては変わらない。取り込み直し・生成し直しも過去の音声を保持する。
音声変更後は字幕の新しい版と動画の再検査が必要。公開品質・演技・発話一致は試聴と最終確認で判断する。

## Macでの準備・動作確認

### WAV方式（VOICEVOXが動かないmacOS 12でも選べる経路）

既存のアプリ起動環境とFFmpegを使う。`ffmpeg -version`、`ffprobe -version`、Python 3.11/3.12を確認する。
FFmpegがなければ、利用者自身がOSに対応する信頼できる配布物を準備する。旧バイナリの自動導入はしない。

```sh
git fetch origin
git switch feature/phase9b1-japanese-voice
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-video.txt
python -m streamlit run app.py --server.address 127.0.0.1
```

作業中ファイルがある場合は別checkoutで確認し、既存作品フォルダをバックアップする。テストは複製または専用作品で行う。
人物・正式脚本を登録し、上記WAVフローを1発話、続けて全発話で試す。アプリを再起動し、試聴・正式割り当てと元WAVが残ることを確認する。
入力は自己完結したRIFF PCM WAV、25MiB以下、1〜2チャンネル、8〜96kHz、8/16/24/32bit、10分以下。
圧縮WAV・浮動小数WAV等はPCM WAVとして事前に書き出す。変換結果は16kHz・16bit・モノラルで検証し、FFmpeg/ffprobeはshellなし、時間・容量上限付きで使う。
元WAVは`voice/original/<画像と独立した音声asset UUID>.wav`、変換後は従来どおり`voice/audio/<UUID>.wav`。

### VOICEVOX方式（実機では未検証）

公式配布物が自分のOS/CPUと音声ライブラリに対応することを確認し、利用者自身でENGINEまたはVOICEVOXをMac上に起動する。
アプリとエンジンを同じMacで実行する。ブラウザだけMacでも、アプリ本体がクラウドならこの接続は使えない。
エンジンの待受もループバックに限定する。OSの安全設定を無効にせず、署名や提供元を確認する。

```sh
MANGA_ENABLE_LOCAL_VOICEVOX=1 python -m streamlit run app.py --server.address 127.0.0.1
```

「ローカルVOICEVOXの準備・利用可否」で取得内容を確認し、チェックとボタンで稼働・話者一覧を取得する。
人物ごとに話者・スタイルを対応付けて保存。発話ごとに規約と送信範囲を確認し、プレビュー生成を押す。
抑揚・音量・話速・音高を変え、別候補が保存されること、保存済み候補の再利用で再合成しないことを確認する。
語尾・固有名詞・間・アクセントは実際に試聴する。エンジン停止時は自動でPollyへ切り替わらず、WAV方式へ明示切替する。

## 公式仕様・互換性の確認記録

確認日：2026-10-25 UTC。以下は公式資料の確認であり、Mac実機・実エンジンの動作証明ではない。

|確認対象|公式資料・確認結果|
|---|---|
|HTTP API|[API案内](https://voicevox.github.io/voicevox_engine/api/)、[ENGINE 0.26.0 OpenAPIスナップショット](https://github.com/VOICEVOX/voicevox_engine/blob/0.26.0/test/e2e/__snapshots__/test_openapi/test_OpenAPIの形が変わっていないことを確認.json)を調査。GET `/version`、GET `/speakers`、POST `/audio_query?text=…&speaker=<style id>`、POST `/synthesis?speaker=<style id>`（JSON body）を使用|
|パラメータ|AudioQueryのspeedScale/pitchScale/intonationScale/volumeScale/outputSamplingRate/outputStereo。talk/streaming_talk対応スタイルのみ選択。アプリは範囲を絞り、出力16kHz/monoを指定|
|Intel配布・署名|[ENGINE 0.26.0リリース](https://github.com/VOICEVOX/voicevox_engine/releases/tag/0.26.0)にmacos-x64 / arm64、Mac署名追加の記載。macOS 13で実行できるようにするビルド変更へのリンクあり。配布バイナリの実署名・公証・依存ライブラリ動作は未検証|
|最低OS|[公式ソフトQ&A](https://voicevox.hiroshiba.jp/qa/)はmacOS 14以降、MacはCPU版の案内。エディタと独立ENGINEの条件は区別する。macOS 12上でENGINE・CORE・音声モデルの組合せが動くことは確認できないため対応済み表示をしない|
|ソフト規約|[VOICEVOXソフト規約](https://voicevox.hiroshiba.jp/term/)：商用・非商用可、音声ライブラリ規約を守る、VOICEVOX利用が分かるクレジットが必要|
|音声固有の条件|例：[ずんだもん等の音源規約](https://zunko.jp/con_ongen_kiyaku.html)は音源別クレジット・共通禁止事項がある。他の音声へ一般化しない。キャラクター画像の権利とも別|
|エンジン/モデル|[ENGINE README](https://github.com/VOICEVOX/voicevox_engine/tree/0.26.0)・配布説明を参照。コードのライセンスだけで音声モデル・出力の利用条件を満たすとは判断しない|

`/speakers`のspeaker_uuid・スタイルID・話者バージョンを検証する。生成直前に一覧を取り直して対応を照合し、エンジン版と話者情報を成果物の出典へ保存する。
接続できた事実だけではmacOS 12全体の対応や特定音声の利用許諾を証明しない。

## 接続・保存・復旧の契約

- 本実装はローカル経路を既定OFFとし、実HTTPはMac + `MANGA_ENABLE_LOCAL_VOICEVOX=1` + 明示操作でのみ許可する。
- 接続先は文字列完全一致の`http://127.0.0.1:50021`または`http://[::1]:50021`。任意URL・DNS名・LAN・別ポート・userinfo・パス・クエリ付き接続先を拒否する。
- HTTPConnectionを数値IPに直接接続する。HTTP_PROXY/ALL_PROXY等を参照せず、リダイレクトも再試行もしない。ネットワーク失敗の生の本文・URL・応答をログ/JSONへ残さない。
- 1発話生成は版・話者一覧のGETとaudio_query/synthesisのPOST。Macローカルエンジンへ読み上げ文・スタイル・設定のみを渡す。画像、作品全体、APIキーは送らない。
- 既存voice.jsonのschema_version=1、トップレベル、旧mock/Polly設定を維持。新規providerの声設定とジョブ/assetのprovenanceを追加する。古いアプリは新providerを認識できないため新しい版で開く。旧データの自動移行はしない。
- 対象確認票は作品UUID・脚本版・発話ID・話者UUID・字幕原文・読み・ページ/コマ・声設定ハッシュ・制作トークンを照合する。音声が本当にそのセリフかは人間が試聴確認する。
- 元SHAと変換後SHAを保存し、試聴・再利用・字幕/動画で検査する。元音声の欠損/変更もエラーとし、ダッシュボードの検査証跡を更新対象にする。
- ジョブ実行を既存世代ロックで直列化し、保存revisionで競合検出。同じ条件・元SHAの候補は再利用する。候補保存も復旧も採用を変更しない。
- 変換/保存失敗後は「履歴・復旧」で元WAVまたはstagingから再開する。再合成しない。受信前/元WAV保存前の中断は未確定として停止し、人間が履歴に残して終了してから新しい操作を行う。
- 脚本の新しい版は明示的に新しい対象確認を要求する。古い版の音声を自動適用しない。音声を差し替えたときは字幕・動画の指紋が変わり、古い出力は履歴に残る。
- 無料自動進行はローカルエンジンを呼ばない。全発話の手動確認・正式割り当て後は、既存の字幕・動画の無料処理へ進める。有料設定は従来どおり停止する。
- 共通予算（作品500円/全作品月5,000円）とPolly/画像の安全ゲートは維持する。無料WAV/VOICEVOX経路は予算台帳を作成・更新しない。
- GitHub作品同期の収集対象は変更しない。従来どおりvoice/videoは作品同期対象外なので、音声のバックアップには作品フォルダとvoice.json/voice配下を含める。同期やSNS投稿は自動実行しない。

## 公開前の確認と未確認事項

保存したクレジットはタイムラインJSONに含み、一覧TXTを取得できる。MP4には自動挿入しないため、利用者が動画内/説明欄等へ掲載する。
規約チェックは利用者の確認記録であり、権利の自動判定ではない。ソフト・音声・録音許諾の範囲と公開先規約を確認する。

フェイク・オフラインテストは接続契約、保存・復旧、字幕の実尺、3形式MP4への素材連携を確認する。
フェイクWAVは日本語の発話情報へ結び付けた検証用の音であり、本物の日本語発音・声質・人物一致・商用品質を保証しない。
macOS 12/Intel実機、実ENGINE/音声ライブラリ、最終の聴感とクレジット掲載は未確認。
Phase 7『君が忘れた、最後の約束』の実作品PDF・MP4完走/目視試聴は未検証のままであり、今回の合成fixtureで完了扱いにしない。

次の改善候補：実機結果の記録、音声辞書/アクセント編集、規約確認済みライブラリの入力支援、字幕と聴感の調整、クレジット表示の編集支援。
