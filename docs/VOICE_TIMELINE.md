# 音声・字幕タイムラインの契約（Phase 3 → FFmpeg）

`manga.voice_contract.validate_timeline()` を共通の入口とします。
既存ストーリーの `schema_version: 1` とは独立した、`format: manga-voice-timeline`、`schema_version: 1` です。
[実際にモックで生成したJSON例](../examples/video_timeline_example.json) は構造の例です。参照するWAV本体はGitへ同梱していません。
`scripts/verify_voice.py --output /未作成のフォルダ` で、その環境の実WAVと対応したJSON/SRTを生成できます。

## フィールド

| 領域 | 保存項目 |
| --- | --- |
| ルート | `id` = `timeline_version_id`（UUID）、`project_id`、`story_version_id`、`story_sha256`、`artwork_revision`、`settings_revision`、`source_fingerprint`、`created_at` |
| 時間 | `duration_ms` と全 `start_ms` / `end_ms` は整数ミリ秒。開始を含み、終了を含まない区間 |
| options | `gap_ms`（発話・コマ間の余韻）、`silent_panel_ms`（無音コマの手動指定尺） |
| assets | 音声の `id`、`job_id`、`speaker_id`、`story_version_id`、`cache_key`、`settings`、`mock`、`human_reviewed`、`rights` |
| 音声ファイル | `assets[].path` は作品フォルダ相対の `voice/audio/UUID.wav`。`info` 内に `sha256`（WAV全バイト）、`duration_ms`、`frames`、`sample_rate` = 16000、`channels` = 1、`size`、`measurement`。16bit PCMであることは読込時に検証 |
| scenes | `id` = p{page}-c{panel}、`page`、`panel`、`description`、区間、`image`、`utterances`、`silent_duration_user_setting` |
| image | 採用画像の `id`、`path`（images/panels/UUID.png）、実バイトの `sha256`、生成元 `story_version_id`。未採用・欠損はnullと警告 |
| utterances | `id`、`speaker_id`、`kind`（dialogue / narration）、元の `text`、実際に読む `spoken_text`、`emotion`、`asset_id`、区間 |
| subtitles | 各発話と同一の `utterance_id`、`text`、区間。読みではなく元セリフを表示。話者名は追加しない |
| 確認 | `needs_review`、`warnings`、`publication_review_required`（常にtrue）、`billing_note` |

ジョブの `request_id` は `job_id` と同じUUIDで、作品内のvoice.jsonと共有費用台帳を照合するキーです。
`rights` はモックならtest-only、Pollyならprovider-stock-voice / user-attestedと確認日・利用規約URLを保持します。ライセンスの自動認定ではありません。
元セリフ・話者名・人物IDの履歴はvoice.jsonにも残ります。APIキーはどの形式にも含めません。

## 尺の決定

WAVは16bit little-endian PCM、16kHz、モノラルです。尺は `ceil(frames * 1000 / sample_rate)` で決めます。
ffprobeがある環境ではcodec・rate・channel・尺も照合し、引数配列で実行します。不在時に文字数から尺を推定することはありません。
同一コマはナレーション→セリフの順です。発話の間とコマ間には指定したgapを入れます。音声がないコマだけsilent_panel_msを用います。
字幕はWAV実尺の区間に一致します。SRTはUTF-8、時刻HH:MM:SS,mmm、本文はHTMLエスケープして出力します。
複数人の同時発話・BGM・SFX・音声途中の切り取りはこの版の対象外です。

## 次のFFmpeg実装が守ること

1. `validate_timeline()` で構造・版・ID・時間・字幕対応を検証し、プロジェクト配下の安全な相対パスへ解決する。リンクやパス脱出を拒否する。
2. WAV・画像の存在、SHA-256、codec、サンプル数を実ファイルと照合する。構造バリデーターだけでファイル存在確認済みとは扱わない。
3. 最新の脚本・人物／音声設定・画像との一致は `timeline_is_current()` または同等のfingerprint比較で検証し、古い出力を黙って使わない。
4. テストトーン・未試聴・画像欠損・古い画像・未確定脚本の警告を人間に提示する。`needs_review: false` でも公開前確認は別途必要。
5. 発話の開始位置にWAVを配置し、字幕と同じミリ秒を用いる。無音区間はローカルで補う。タイムラインに合わせて音声を途中で切らない。
6. 保存先・容量・プロセス制限・一時ファイル・キャンセル・原子的確定を実装し、音声生成APIをFFmpeg工程から呼ばない。

focus、字幕セーフエリア、横型／縦型構成、承認済み短縮台本、BGM/SFX権利、MP4出力履歴はPhase 4で独立形式として追加します。
今回のJSONを、未実装の動画編集仕様まで満たす完成動画データとは扱いません。元画像やcomposition.jsonを音声工程で変更しません。
