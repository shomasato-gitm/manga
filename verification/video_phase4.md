# Phase 4：FFmpeg動画生成の検証記録

確認日：2026-10-24（日本時間）。Issue #4、添付phase4_codex_instruction.mdを実装仕様として使用しました。
PR #5のマージをGitHubで再確認し、最新main `807914aeffb0865cba3d948d23e113cacddc7fcc` から `feature/video-renderer` を作成しました。
mainへの直接変更・自動マージ・SNS投稿は行いません。

## 実装したものと変更ファイル

| ファイル群 | 内容 |
| --- | --- |
| manga/video_models.py | 3形式の独立計画、コマ／完全な1発話の選択、順序、focus、整数msから累計フレームへの変換 |
| manga/video_sources.py | 既存voice_contractと実ファイルの版・SHA・PNG・PCM照合、欠損箇所の表示 |
| manga/video_visuals.py | 全体フィット、ぼかし背景、重要領域を含むcrop、穏やかなズーム／パン、日本語字幕PNG |
| manga/video_audio.py | 元WAVを正確なサンプル位置へ配置し、無音区間も継続 |
| manga/video_process.py | 引数配列、プロトコル制限、期限・ログ上限、プロセス群の中断、ffprobe検証 |
| manga/video_renderer.py | クリップキャッシュ、画像・字幕・音声・任意BGM/SFXの合成、3形式H.264/AAC |
| manga/video_storage.py / video_jobs.py / video_service.py | video.json、版競合・作品ロック、バックグラウンドジョブ、原子的完成確定、履歴、無料再出力・保存復旧 |
| manga/video_ui*.py / navigation.py | 6工程を維持した任意画面、計画・プレビュー・検査・進捗・中断・履歴・MP4取得 |
| scripts/verify_video.py / video_measurements.py | 8ページ24コマの完全新規fixture、実MP4と同期の測定 |
| tests/test_video_*.py / video_fixtures.py | 追加45件の単体・異常系・実FFmpeg・UIテスト |
| .github/workflows/pytest.yml | 既存Python 3.11/3.12の全pytestにFFmpeg導入、新作業ブランチのpushも検証 |
| README / docs | 操作・Mac手順・データ契約・中断復旧・Phase 3からの接続の更新 |

音声・漫画・予算の製品コードと保存形式は変更せず、動画をvideo.jsonとvideo/へ分離しました。
既存のworks/、data/projects/、人物ID・参照画像・候補画像・音声・台本・ページ編集・評価・GitHub同期データを変更・削除していません。
生成したMP4・PNG・WAVバイナリはソースGitへ追加しません。追加pip依存はありません。

## 変更前後の全pytest

| 実行 | 成功 | 失敗／エラー／スキップ | pytest表示時間 |
| --- | --- | --- | --- |
| 変更前main、Python 3.12.14 | 585 | 0 / 0 / 0 | 110.39秒 |
| 変更後、Python 3.11.17 | 630 | 0 / 0 / 0 | 205.74秒 |
| 変更後、Python 3.12.14 | 630 | 0 / 0 / 0 | 200.36秒 |

```bash
python -m pytest -q --junitxml=/tmp/video-final.xml
```

既存の漫画制作・音声・予算・同期・ページ編集・レビュー・UI回帰に加え、新しい45件を含みます。
最初のUIテストで見つかった履歴セレクターの参照とキャンセル時の選択形式の問題は修正後に再検証しました。
短いシーンのフェードをカットへ変更し、字幕を画像フェードの後に合成する点も、実フレームを見て修正しました。
CIはFFmpegを導入したUbuntu 24.04で全pytestを実行します。CI成功状態はPRのChecksで確認し、ローカル成功をCI成功と読み替えません。
機械可読のJUnit集計・実動画メタデータは [video_phase4_tests.json](video_phase4_tests.json) を参照してください。

## 実MP4の結果

環境：Linux x86_64、FFmpeg **7.1.5**、libx264、Pillow、Noto CJK、Streamlit 1.40.2。

```bash
python scripts/verify_video.py --output /workspace/artifacts/video-phase4-final-20261024
```

完全新規の8ページ24コマ『君が忘れた、最後の約束（動画検証用）』に図形の人物PNGを採用し、3人物＋ナレーターの確認用トーンを保存しました。
実ユーザーの作品・画像・音声は使用しません。同じ素材から本編24シーン、縦型各3シーンを出力し、Shortsは順序とフック文を変えています。

| 実ファイル | 寸法 | 映像／音声 | FPS | 実測尺 |
| --- | --- | --- | --- | --- |
| youtube_main.mp4 | 1920×1080 | h264 / aac、yuv420p、48kHz・2ch | 12 | 3,417ms |
| tiktok_vertical.mp4 | 1080×1920 | 同上 | 12 | 1,250ms |
| youtube_shorts.mp4 | 1080×1920 | 同上 | 12 | 1,250ms |

これは軽量検証用12FPSの短尺fixtureです。本番の既定24FPSや目標3〜5分／45〜75秒を達成した完成作品とは扱いません。
本番計画は実尺を表示し、素材が目標尺に足りない場合はレビュー警告を残します。
ユーザー画像と日本語の人声による聴感・演出の品質確認は未実施です。

ffprobeで寸法・H.264・AAC・yuv420p・FPS・映像／音声ストリーム・尺を照合しました。
MP4の0.1秒フレームを画像化して確認し、左右端と中央の3人物が表示され、日本語字幕・Shortsフックが別領域に描画されることを確認しました。
重要領域のcrop可否と最大4%モーション時の余白は、幾何計算のテストでも検証しています。顔を画像認識で検出したという意味ではありません。

同期はMP4をデコードし、既知のトーン周波数の開始と、字幕PNGのマスクに一致するフレームを測定しました。
3形式とも**音声開始の最大誤差5ms、字幕開始の最大誤差70ms**で、±250ms以内でした。
この測定は専用トーンfixtureの結果で、任意の日本語音声を認識・評価する機能ではありません。

実行したFFmpeg引数配列の抜粋は [video_phase4_commands.json](video_phase4_commands.json)。
全コマンド・シーンごとのcrop／配置情報・字幕区間は、作品のvideo/jobs/JOB_ID/render_receipt.jsonへ保存します。
実コマンドは `base.png → zoompan → 字幕PNGの時間指定overlay → libx264` と、`concat + 配置済みvoice.wav + 任意音声 → 映像copy/AAC/faststart` の2段階です。
ユーザーのセリフやアップロード名をfilter式やshellへ挿入しません。

## エラー・保護の確認

- 画像／WAVの未採用・欠損・破損・空・SHA不一致、symlink、パス脱出、古い脚本・タイムラインを停止。
- 字幕の長文・未対応文字・特殊記号、FPS・focus・不正なmotion、NaN/Infinity/ゼロのMP4検証値を拒否。
- FFmpeg不在でも動画だけを停止し、漫画・音声画面を利用できる。
- 同じ作品の二重ジョブ・実行中の計画変更・保存競合・空き容量不足を拒否。
- 実子プロセスでタイムアウトと中断を試し、プロセス群を終了。
- FFmpeg失敗では完成履歴を作らず、APIなしで再出力できる。
- MP4確定後のメタデータ保存失敗から、同一ジョブで上書き・重複記録なしに保存を再開。
- 同じ完成計画はFFmpeg再実行なしで再利用し、明示的な再出力はクリップを再利用して新しい履歴を作る。
- BGM/SFXの権利未確認を拒否し、登録後の実ミックスとフェードを確認。無音だけのハイライトにも音声トラックを保持。
- 動画処理前後の既存JSON・画像・WAVハッシュを照合し、変化なしを確認。
- 非表示の動画画面では素材検査・レンダリングを実行しない。MP4は利用者が選んだ1本だけ読み込む。
- 同じfixture出力先へ再実行して拒否され、既存292ファイルのハッシュが変わらないことを確認。

全新規テストは外部ネットワーク・有料APIの実行を拒否します。**実課金呼び出し0回、API費用0円**。
共通予算台帳の上限・予約・請求不明時の停止条件は変更していません。

## 操作と残る制約

Macでの導入・更新・画面操作・保存先・中断復旧は [VIDEO_RENDERING.md](../docs/VIDEO_RENDERING.md) にまとめました。
Intel Mac/macOS 12実機、QuickTime実機再生、長尺3〜5分を古いMacで生成する所要時間は未検証です。
比較的新しいlibx264対応FFmpeg/ffprobeが必要で、旧OSでは対応パッケージ・ビルドを利用者が確認します。
強制終了・停電では実行中記録や一時ファイルが残るため、保存再開／中断確認と再出力が必要です。
ステージ素材・キャッシュ・履歴は自動削除せず、容量とバックアップを利用者が管理します。複数作品の並行生成はCPU・メモリを消費します。
本番の尺・構成の意味・顔／重要情報・字幕・利用権・投稿先ポリシーは人間の最終確認が必要です。SNS投稿は実装しません。
次の漫画自動生成は既存StoryStore/ArtworkStore→音声タイムライン→動画計画へ接続でき、既存JSONへの破壊的変更はありません。
