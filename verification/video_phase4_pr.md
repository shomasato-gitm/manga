## 変更内容

既存の採用PNG・Phase 3のWAV／字幕タイムラインから、YouTube本編1920×1080、TikTok／Shorts各1080×1920のH.264/AAC MP4をローカル生成します。Closes #4。

- 横型は元の全コマ・尺を維持。縦型2形式は独立したコマ／発話選択・順序・focus・フック文を持つ。
- 緩いズーム／パン、カット／フェード、重要領域を含められない時の全体フィットとぼかし背景。
- 日本語の禁則・フォント検証・背景／縁取り付き字幕、実WAVサンプル基準の音声配置、任意の利用権確認済みBGM/SFX。
- 「完成・書き出し → 動画制作・MP4出力」に編集・静止画プレビュー・素材検査・バックグラウンド生成・中断・MP4再生／取得・履歴／復旧を追加。
- video.jsonとvideo/へ分離。版・SHA・codec・実尺照合、ロック・保存競合、引数配列、プロトコル／容量／時間制限、検証後の原子的確定。
- 同じ素材のキャッシュと無料再出力。作品JSON・人物ID・画像・音声・予算・既存GitHub同期を維持。

## 検証

変更前585成功 → 変更後Python 3.11／3.12で各**630成功、0失敗・0エラー・0スキップ**。追加45件には実FFmpeg・異常系・Streamlit UIを含みます。
CIへFFmpeg導入を追加し、既存の両Pythonで全pytestを実行します。CI結果はChecksを参照してください。

8ページ24コマの完全新規図形PNG・確認用トーンを採用した専用作品で、3形式の実MP4を生成・ffprobe検証しました。
短尺検証用12FPS、実尺は本編3.417秒・TikTok1.250秒・Shorts1.250秒。すべてh264/aac・yuv420p・48kHz/2ch。
音声・字幕をMP4からデコードして測定した最大誤差は、音声5ms・字幕70ms（目標±250ms以内）。
有料API実行**0回・費用0円**。動画工程からTTS/APIを呼びません。共通BudgetedExecutorの上限は変更していません。

## 文書と残るリスク

[操作・Mac手順](https://github.com/shomasato-gitm/manga/blob/feature/video-renderer/docs/VIDEO_RENDERING.md) / [変更ファイル・テスト・実MP4検証](https://github.com/shomasato-gitm/manga/blob/feature/video-renderer/verification/video_phase4.md)。
既定は24FPS、目標本編3〜5分・縦型45〜75秒。短いfixtureを本番尺達成と扱わず、実尺未達は警告します。
Intel Mac/macOS 12・QuickTime実機・本番長尺性能・実作品の演出品質は未検証です。顔の自動検出は行わず、注目領域は人間が指定します。
比較的新しいlibx264対応FFmpegが必要。履歴・キャッシュ・一時素材は自動削除せず、保存容量の管理が必要です。
公開前レビュー・素材権利・投稿先ポリシーは人間が確認します。SNS投稿・新しい有料API・自動マージは含みません。

main 807914aからの独立ブランチです。ドラフトPRとし、mainへ自動マージしません。
