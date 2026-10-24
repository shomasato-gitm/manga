# PR #2：API予算管理のレビュー・修正・検証

対象：[PR #2](https://github.com/shomasato-gitm/manga/pull/2)、`feature/video-budget-core-20261024`。
レビュー開始時のheadは `574f52214a37930e4febc074503b7794d3ddd571`、base/mainは `0e9ed3fa2c2311a198e4cd35a65110efc2f72ac5`。
GitのPR headとブランチheadが一致し、PRは未マージのドラフトであることを確認してから作業した。

今回の範囲は添付CODEX_01_REVIEW_PR2.md。CODEX_02_TTS.md／CODEX_03_FFMPEG.mdは依存整理だけを行い、実装を開始していない。
添付AGENTS.mdの規則を読み、既存リポジトリへ無条件でコピー・上書きしていない。

## 実装済み：確認した問題と修正

| 問題 | 修正・検証 |
| --- | --- |
| 予約を少し超えた実績でも500円未満なら追加処理を許していた | 実績をそのまま保存し、未確認の見積超過がある間は全作品の新規予約・実行を停止。理由付きの明示確認だけで解除し、実績は消さない |
| 月替わりに過去月の未確定予約が当月枠へ算入されなかった | 予約・請求不明分を全月から持ち越す。精算済みは請求確定月へ計上し、作品500円は生涯累計のまま |
| 実行中もstate=reservedのため、未送信キャンセルや第三者精算で枠を解放できた | 実行権とclaim IDを先に永続化。実行権取得後のキャンセル・実行権なしの第三者精算を拒否 |
| 金額だけで実行でき、料金表の確認日・モデル・為替等の根拠を必須にしていなかった | PriceQuote・全最大利用量・retry_limit=0を必須にし、期限・モデル・税・為替を検証。見積根拠と予約額の対応も保存・再検査 |
| プロセスごとに別の上限を指定できた | 同じ台帳の設定を固定し、不一致なら停止。UI／後続アダプター用の共有open_budgetを追加 |
| settledでactual=NULL／負数などの不整合を正常な残額として扱えた | SQLiteの整合性・状態・金額・見積対応を検査し、破損・管理テーブル欠損・不整合時は新しい処理を停止 |
| Decimalの通常精度で500円を極小量超えた見積を切り捨てる場合があった | 入力精度を確保してマイクロ円へ切り上げる。NaN・Infinity・負数・過大値も拒否 |
| UIの月間残額・未確定件数が不足し、DBエラーが画面例外になった | 作品／月間残額・全作品未確定件数・停止理由を表示。小額をゼロと見せず、エラー時に復旧案内を表示 |
| 再起動時のDB喪失で空の台帳を作り直し得た | 初期化記録を追加し、記録があるのにDBがない／空なら停止。外部永続先MANGA_BUDGET_DBも案内 |

API例外、KeyboardInterrupt／SystemExit、利用量欠落、不正実績、精算失敗、不明状態の保存失敗も検証した。
実行権を取得した予約は、不明状態の保存自体が失敗しても保持し、再起動後の同じrequest_idで再送・自動解除しない。
旧台帳のreservedは送信済みか識別できないため、元の行と金額を変更せず保全用の実行権を追加し、明細の手動確認を要求する。

## 変更ファイルと既存機能

- `manga/budget/ledger.py`・`guard.py`・`pricing.py`・`__init__.py`：既存の予約・実行・精算・料金見積の修正。
- `manga/budget/integrity.py`・`config.py`：台帳検査、見積対応、共有保存先。
- `manga/budget_ui.py`：予算状況・台帳エラーの表示。
- `tests/test_video_budget.py`・`test_video_budget_ui.py`：新しい安全な見積契約をモックで適用。月替わりテストでは精算済みと未確定を区別し、UIテストは利用者のMANGA_BUDGET_DBを参照しない。
- `tests/test_budget_review.py`・`test_budget_lifecycle.py`・`test_budget_app.py`・`budget_fakes.py`：再現・複数プロセス・異常系・画面テストと架空の料金表。
- README・予算ガイド・次Phase依存文書・本検証記録・件数JSON。
- `.github/workflows/pytest.yml`：既存3.11／3.12マトリクスを維持し、PR #2だけに実際のCI結果を追記する専用ジョブを追加。

元のPR全13ファイルも確認した。navigationは6工程を維持し、予算画面だけを遅延読み込みする。
video_prereqsは実行ファイルの存在確認だけ、requirements-video.txtは任意機能の説明だけで、必須SDKは追加していない。
`.gitignore`のdata/runtime除外はDBと初期化記録にも適用され、GitHub作品同期の対象ファイルを増やしていない。

`git diff origin/main -- works data/projects` は空。
既存のmodels・story/artwork保存・GitHub同期・ページ合成／出力・requirements.txt／requirements-dev.txtは今回変更していない。
作品JSONのschema_version: 1、人物ID、画像、保存履歴に破壊的変更はない。
新しい予算APIの呼び出し契約は強化しており、未マージのraw quote_jpyだけの例とテストを更新した。既存漫画機能にその呼び出し元はない。

## 実行したテスト

Linux x86_64、Streamlit 1.40.2、日本語Notoフォント、既存requirements-dev.txtで実行。
表はpytest表示時間。機械可読の[件数JSON](pr2_budget_tests.json)のsecondsはJUnit testsuiteの集計時間。

| 対象 | Python | 成功 | 失敗・エラー | スキップ | pytest表示時間 |
| --- | --- | ---: | ---: | ---: | ---: |
| 修正前の全pytest | 3.12.14 | 480 | 0 | 0 | 93.03秒 |
| 最終コードの全pytest | 3.11.17 | 526 | 0 | 0 | 96.73秒 |
| 最終コードの全pytest | 3.12.14 | 526 | 0 | 0 | 95.29秒 |
| 最終コードの予算コア・任意ツール検出 | 3.9.25 | 69 | 0 | 0 | 1.44秒 |

実行：

```bash
python -m pytest -q --junitxml=<一時保存先>/pytest.xml
python -m pytest -q tests/test_video_budget.py tests/test_budget_review.py tests/test_budget_lifecycle.py tests/test_video_prereqs.py
```

最初の再現テスト8件は修正前に8件とも失敗した。修正後はこれらを含む46件の追加テストが成功。
既存480件も全件実行し、旧6工程UI、画像・PNG/PDF・GitHub同期等の回帰を確認した。
新しいAppTestでは、予算表示・破損時の案内・漫画編集へ戻る操作と既存作品ファイルの不変を確認した。
spawnした4プロセスによる作品500円・全作品月間5,000円・プロバイダー枠・重複IDの単一実行も含む。
新しい予算コードはPython 3.9構文検査も通過した。

## CIとPR記載

レビュー開始時の対象ブランチの直近CIは複数のcancelled実行で、成功とは扱わなかった。
修正後のGitHub Actionsは別の実績であり、上記ローカル成功をCI成功と読み替えない。
[PR #2のChecks](https://github.com/shomasato-gitm/manga/pull/2/checks) と、専用報告ジョブのSummary／PR本文の検証ブロックで実際のコミットと件数を確認する。
JUnit・件数JSONはActionsのArtifactsへ保存する。

報告ジョブはPR #2・同一リポジトリ・指定ブランチに限定し、リポジトリコードをcheckout／実行しない。
現行headとopen状態を照合し、ETag付き更新で元のPR説明を残して検証ブロックだけを追加・更新する。
更新が拒否された場合はコメント、コメントも拒否された場合はSummaryに結果を残す。
ドラフト状態を変更せず、PR作成・承認・マージ・本番公開はしない。

## 実課金と未実装・未確認

**課金対象プロバイダーのAPI呼び出し0回、API費用0円。** テストの単価・モデル・応答は架空データとローカル関数だけを使用した。
認証情報の既知形式を変更ファイルで静的検査し該当なし。新しいコードはAPIキーを取得・保存・ログ出力しない。

- **未実装**：実プロバイダー接続、TTS・WAV・字幕、音声キャッシュ／ジョブ、FFmpegによるMP4、公開操作。今回は次Phaseの[依存関係と契約案](../docs/video-next-phases.md)だけを整理した。
- **未確認**：Intel Mac実機、Python 3.10実機、Python 3.9でのStreamlit全回帰、実請求、現行の本番モデル料金、急な電源断。成功した範囲と区別する。
- **残リスク**：全ワーカーが同じ永続SQLiteと上限設定を共有する必要がある。別端末・別DBは合算できない。DBと初期化記録を両方失う／過去へ戻す操作まで実請求を復元するものではない。
- **残リスク**：公式料金・モデル・税・最大請求量の正当性、SDK内の再試行無効化は後続アダプターで確認する。コールバック内部の多重送信をこの基盤から自動検知できない。提供元の最終請求額を厳密に保証するものではない。
- **残リスク**：強制終了や記録失敗で実行権付きreservedが残る場合は手動照合する。SQLite書き込み可能なローカル保存先を使い、全ワーカー停止後に台帳と初期化記録をバックアップする。

mainは変更せず、同じ作業ブランチへレビュー修正を追加する。マージ判断は人間が行う。
