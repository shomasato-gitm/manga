# Phase 9-A 実装・検証記録

- 基点main：59da1ac96f06b4edfbf959e32a51d4d97aec5cb0（PR #15マージ）。ブランチ feature/phase9a-api-architecture。
- 設計資料4本、調査専用JSON、独立した読取専用CLI、オフラインテスト。既存UI/Service/Store/JSON/UUID/採用/予算/同期/自動進行/本番ガードは変更しない。
- 公開公式資料を2026-10-26（日本時間）にweb/GitHubで確認。生成/認証/モデル一覧/token数照会/VOICEVOX接続0回、費用0円。
- 料金参考値を実行許可にしない。Geminiの既存停止定数/manifest/readinessと予算ガードはそのまま。調査JSONは実行カードと異なるformat。
- 既存の1承認複数request、Responses/Polly低水準送信防御、発話WAV import未実装を9-Bの課題として明記。
- VOICEVOXのmacOS14以降という現行案内とmacOS12の差、OpenAI候補のDeprecated表示、実権限/最大請求/品質未確認を記録。

## ローカル検証と環境障害

修正前のPython3.11全pytest：974件成功（638.29秒）。

新規診断テスト36件成功。フェイクの全キー/全flag ON、秘密の非表示、ネットワーク/DNS/SQLite/予算初期化/保存の禁止、資料欠損/不正/未取得/未来/期限切れ、日本時間の日付境界、loopback偽装拒否、旧Gemini停止と500/5000円、CLIエラー秘匿を検証。

実CLIでも、日本時間の当日判定、実行無効、network_calls=0、budget_reservations=0、残予算未取得を確認した。

最終全pytestはPython3.11/3.12の両プロセスで終了コード0を確認。その直後にexec-serverが切断し、実行環境がunavailableになった。ローカルXML/詳細ログと未コミットの作業を直接取得できなくなったため、件数・時間を推測で埋めていない。

会話内に残る診断99行・CLI22行・テスト145行、調査JSON548行の実装をGitHub連携で復元。資料は内容を再整理して保存した。既存ファイルには触らず追加のみとし、復元版はGitHub ActionsのPython3.11/3.12でも全件再検証する。結果は phase9a-api-tests.json とPRのCIを参照。

## コマンドと確認範囲

~~~bash
python scripts/diagnose_api_safety.py
python -m pytest -q tests/test_api_architecture_diagnostics.py
python -m pytest -q --junitxml=test-results/pytest.xml
~~~

全pytestには既存AppTest、未確認採用拒否・確認票・予算上限・不明請求・二重実行・差分更新・保存復旧・自動進行・同期・PNG/PDF/音声字幕/3形式MP4を含む。

HTTP redirect応答やVOICEVOX生成のtransportは今回未実装。URL判定のオフライン方針を検査しただけで、実接続を検証済みとはしない。Mac実機・実engine・実API請求・生成品質・Phase 7実作品のPDF/MP4品質確認は未検証。新依存・破壊的移行・既存データの削除なし。
