## 変更内容

保存済み漫画の人物・ナレーションから音声を制作し、同じ実測尺でSRTとFFmpeg用タイムラインを出力します。Closes #3。
6工程は維持し、「完成・書き出し → 音声制作・字幕」から準備・生成・試聴・履歴復旧を行います。

- 人物ID別の声、発話別の読み・感情・速度、曖昧な話者の手動確認。
- 内容ハッシュキャッシュと変更発話だけの再生成、音声候補の履歴・再採用。
- 公式比較後、Polly Standard / Neuralを実装。既定はAPIなしの確認用トーン。
- BudgetedExecutorで作品生涯500円／全作品月間5,000円を共有。確定脚本・公式確認済み料金・機能フラグ・その都度の承認を必須化。
- タイムアウト／利用量不明時は予約保持・再送なし。保存失敗は同じジョブの保存だけ再開。
- WAV実フレームと任意のffprobeを使用し、版・SHA・人物・画像を追跡した独立JSONとSRTを履歴保存。
- 仕様・費用方針・Mac手順・APIなしの検証スクリプトを追加。追加pip依存なし。

## 検証

変更前: main 361444b / Python 3.12、526成功。
変更後: Python 3.11と3.12で各585成功・0失敗・0スキップ。うち追加59件。Streamlit全アプリからのUI、HTTP異常、予算、競合、失敗復旧を含みます。
補助的にPython 3.9の音声コア55件も成功しました。
8ページ24コマ・3人物＋ナレーターのモックで5件生成→5件キャッシュ→変更1件だけ再生成、旧6WAV保持・SRT・JSON保存を確認。
実課金API呼び出し **0回、費用0円**。CIは既存Python 3.11／3.12の全pytestを実行し、結果はChecks/JUnitに保存します。

詳細: [検証記録](https://github.com/shomasato-gitm/manga/blob/feature/video-voice/verification/voice_phase3.md) / [公式料金比較](https://github.com/shomasato-gitm/manga/blob/feature/video-voice/docs/tts-provider-comparison.md) / [操作手順](https://github.com/shomasato-gitm/manga/blob/feature/video-voice/docs/VOICE_PRODUCTION.md) / [タイムライン契約](https://github.com/shomasato-gitm/manga/blob/feature/video-voice/docs/VOICE_TIMELINE.md)。

## 費用・互換性・制約

Polly Standard $4／100万文字、Neural $16／100万文字。24発話・日本語3000文字・Neural・仮定160円/USD・税1.10・安全係数1.25なら、参考換算8.448円、SSML全バイトによる予約例36.7488円。実為替や最終請求額の保証ではありません。
料金例の確認フラグはfalseで配布し、有料実行は別の明示承認まで無効です。
既存story/schema_version:1・人物UUID・画像・編集・評価・同期形式は変更せず、voice.jsonとvoice/へ分離。音声バイナリをGitへ追加しません。

残るリスク: Intel Mac実機・実Polly音声／請求は未検証。Neuralささやきは小声の近似です。共有予算DBと初期化記録のバックアップが必要。応答保存前のディスク故障は手動照合が必要です。
FFmpegによるMP4生成・SNS公開は次PRの範囲です。mainへ自動マージしません。
