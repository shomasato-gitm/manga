# Pull Request作成用の本文

自動作成はGitHub APIの `403 Forbidden` により未完了です。Gitの通常pushは成功しています。
GraphQL `/graphql` とREST `/repos/shomasato-gitm/manga/pulls` の両方で拒否されました。

- 作業ブランチ：`feature/github-studio-sync`
- ベース：`main`
- タイトル案：GitHub作品同期：候補取得・明示採用・競合保護
- [PR作成画面](https://github.com/shomasato-gitm/manga/compare/main...feature/github-studio-sync?expand=1)
- 以下を本文に使い、GitHubの **Create pull request** で作成してください。mainへのマージは行っていません。

---

ChatGPT等がGitHubへ保存した作品JSON・画像を、アプリから直接取得して確認・採用できるようにします。返答JSONの手動コピーを減らし、採用済み作品と完成PNG・PDFを作品専用ブランチへ保存できます。

- 「完成・書き出し → GitHub作品同期」を追加。取得・候補検証・差分確認・明示採用・送信を分離し、6工程メニューと既存の手動取り込みを維持。
- HTTPS Gitによる固定コミット取得、SHA-256付きmanifest、版と競合の検出、人物ID・基準画像の固定、変更前後の保全と採用保存の再開に対応。
- 作品ごとに新しいスナップショットを保存。main/masterへの保存、既存正式ファイルの上書き、強制push、全ローカル作品の一括送信は行わない。
- 既存の制作提案・画像レビュー検証へGitHubの回答JSONを接続し、未採用・未確認として保持。品質レビュー、改善回数上限、点数基準、停止理由をPDCA履歴へ記録。
- 完成PNG・PDFの明示保存、1ファイル10MiB・1回100MiB等の容量制限、認証情報を保存しない運用、日本語ガイドを追加。実行パッケージの追加なし。OpenAI API等の導入なし。

検証：変更前396件成功。変更後はPython 3.11.17／3.12.14の両方で438件成功・失敗0件（既存396件＋追加42件）。詳細は `verification/github_sync_tests.json`。指定作品 `2d83c11f-04ee-4134-a66d-dace81c6f698` の8ページ・24コマ、カラー画像3枚、人物ID、PNG・PDFを実GitHubで往復してハッシュと元資料の保持を確認。
検証ブランチ：`studio-sync-test/2d83c11f-04ee-4134-a66d-dace81c6f698-16edd229`、コミット `4911f3cbe4eb5803ce8c2eec9777aaed45ce963f`。記録は `verification/github_sync_roundtrip.json`。

既知の制約：ChatGPT側のGitHub書き込みは実際に利用可能な連携・権限が必要。提供画像は1ページの3枚のみで、検証PDFの他ページはプレースホルダー。人物の正式基準画像は元資料でも未照合。他端末の目視合格を現在の作品へ自動移植しない。Git LFS・SNS ZIPの同期・自動背景同期は対象外。Mac実機のキーチェーンは未検証。

このPRはレビュー用です。ユーザー確認なしでmainへマージしません。
