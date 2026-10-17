# AI漫画スタジオ 制作状況確認（2026-10-17）

## 確認対象
- GitHub: `shomasato-gitm/manga` / `main`
- プロジェクト指示: AI漫画スタジオ 共通指示
- 制作運用: `docs/chatgpt-project-guide.md`
- JSON例: `examples/story_8pages.json`
- 編集評価仕様: `manga/editorial_models.py`

## 確認できた事実
- 漫画制作アプリとストーリー管理・編集評価・作画指示・ページ編集・品質レビュー・原稿出力の実装が存在する。
- `data/projects/` は `.gitignore` に指定されており、GitHub追跡対象は `.gitkeep` のみ。
- `examples/` のJSONは見本・デモであり、制作対象の確定作品とはみなさない。
- `works/` に追跡済み作品ファイルは存在しない（確認時点のmainブランチ）。
- ローカルStreamlitアプリ側の未同期作品、生成画像、確定状態はGitHubからは判断できない。
- アプリ内のChatGPT入出力は手動であり、OpenAI APIの自動連携は前提としない。

## 制作データ規約
- ストーリーは既存の `schema_version: 1` を維持。
- 最上位: `schema_version`, `title`, `summary`, `pages`
- ページ: `page`, `summary`, `panels`
- コマ: `panel`, `description`, `dialogue`, `narration`
- 編集評価の8軸: opening / characters / structure / emotion / dialogue / foreshadowing / ending / originality。
- 個別版への評価の紐づけを守り、評価済み・改善済み・確定済みの状態を混同しない。

## 次に実行可能な工程
1. 対象作品の確定資料を特定する（GitHub外のローカル保存分がある場合はそれを取り込む）。
2. 作品設定と既存ストーリーを検証し、既存JSONを維持した草案を作る。
3. 編集評価→対象を絞った改善→再評価を反復する。
4. 採用版確定後、人物設定・ページ/コマ別作画指示を生成する。
5. 画像・吹き出し・原稿をレビューして、適切な保存形式で出力する。
6. 確定作品のみ `works/<作品ID>/` 等の管理対象パスへ明示的にGitHubコミットする。

## 実行状態
- 完了: リポジトリの構造・制作ルール・JSON契約の調査。
- 未実施: 特定作品に対するストーリー生成、採点、改善、作画、最終原稿作成。
- 理由: 既存作品の確定資料がGitHub追跡対象に存在せず、サンプルを実作品として誤採用しないため。
- 本レポートは制作状況の監査記録であり、作品の確定を意味しない。
