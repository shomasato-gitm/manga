# ストーリー制作スタジオ：概要3案・参考技法・評価改善PDCAを統合

参考技法を使った概要3案の比較・採用から、詳細脚本の評価・改善・再評価・最終確認までを「企画・ストーリー → ストーリー制作スタジオ」にまとめます。
従来の詳細編集・参考作品・履歴・画像・ページ編集機能は維持します。

## 変更

- 概要3案と選択状態、依頼ID、生成元、参考作品の版・分析ID・応用技法をstory_concepts.jsonに保存。story本体はschema_version: 1を維持。
- 最新脚本と最新評価の対応を検証し、評価→改善→再評価をユーザー操作で反復。初期値は目標85点・改善目安3回。追加改善と最終確定は明示承認。
- 各版の総合点・8項目、実際のJSON差分、改善理由、点数低下とAIの矛盾指摘を比較。
- 概要のみのGitHub同期、未採用の回答受信箱、人物ID・基準画像・PDCA履歴の復元を追加。既存のロック・変更前後保全・再開処理を利用。
- 日本語UI・ガイド・依頼ID付き架空サンプルを追加。OpenAI API・非公式API・ChatGPT画面の自動操作は追加しない。

## 検証

コードコミット: `a3aa85b84528b4d208b33d6f837b2a2ca3dede70`。

- 変更前: 452件成功。
- 変更後のローカルPython 3.11.17／3.12.14: それぞれ486件成功、失敗・エラー・スキップ0件。
- [GitHub Actions実行39145022063](https://github.com/shomasato-gitm/manga/actions/runs/39145022063): Python 3.11／3.12の全pytestジョブ成功。JUnitと件数JSONはArtifactsに保存。
- 追加34件で概要3案、8ページ24コマ、改善3サイクル、UI操作、版・評価の照合、履歴復元、Git往復、競合、中断復旧、元データ保護を検証。
- works・data/projectsは変更していない。認証情報の既知形式の静的検査は該当なし。

詳しくは[検証記録](story_production_studio.md)と[日本語ガイド](../docs/story-production-studio.md)を参照。

## 残る制約

- AI評価は主観的な参考値。意味上の独自性や矛盾は自動保証せず、人間が最終確認する。
- 新機能の同期先も更新が必要。8MiBの制作履歴上限時は削除せず停止。
- Intel Mac実機と突然の電源断は未検証。更新前にMANGA_DATA_DIR全体をバックアップする。
- Streamlit経由のAltair非推奨警告と、既存ActionsのNodeランタイム移行警告がある。実行は成功。

mainへのマージは利用者が判断する。

---

PRの自動作成はGitHub側からHTTP 403で拒否されたため未完了。
[比較・PR作成画面](https://github.com/shomasato-gitm/manga/compare/main...feature/story-production-studio?expand=1)でbaseをmain、compareをfeature/story-production-studioにし、上記タイトルと本文を転記して「Create draft pull request」を選ぶ。
自動作成を再試行する場合は、権限を持つ利用者がリポジトリの「Settings → Actions → General → Workflow permissions」のPR作成許可と組織ポリシーを確認し、対象Actionsを再実行する。許可できない場合も手動でPRを作成できる。
