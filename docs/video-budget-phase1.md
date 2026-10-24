# 動画制作：API予算基盤（PR #2）

本機能は将来の有料APIを接続する前の予算ゲートです。**APIアダプター・音声生成・動画生成は未実装で、今回の開発・CIのAPI呼び出しは0回、実費0円です。**
既存のChatGPT手動連携、作品JSON、人物ID、画像、6工程UI、PNG/PDF/縦読みZIP、GitHub同期は変更しません。

## 予算と永続保存

- 作品IDごとの生涯累計上限：既定500円。暦月でリセットしません。
- 全作品合計の日本時間の月間上限：既定5,000円。作品枠とは独立です。
- 任意のプロバイダー別月間上限も、同じトランザクションで検査します。
- 上限は同じSQLite台帳を共有する全ワーカーに適用されます。別DB・別端末の台帳は合算できません。
- 同じ台帳の上限・プロバイダー設定がプロセスごとに異なる場合、弱い設定へ切り替えず停止します。独自上限は新規台帳の初期化時に指定でき、既存台帳の上限をコンストラクターで上書きしません。

既定保存先は `<プロジェクト保存先の親>/runtime/budget.sqlite3` です。
通常は `data/runtime/budget.sqlite3`。`data/runtime/` はGit管理対象外で、作品同期にも含めません。
UIと今後のアダプターは `manga.budget.config.open_budget(projects_directory)` で同じ台帳を開きます。
コード更新・ZIP展開から分離する場合は、全ワーカーで同じ外部保存先を指定してください。

```bash
export MANGA_BUDGET_DB="$HOME/MangaStudioRuntime/budget.sqlite3"
```

予算画面は「完成・書き出し → API予算管理」にあります。保存先、作品残額、月間残額、作品／全作品の請求未確定件数、全体の停止理由を表示します。
未確定予約は実請求0円と表示せず、予約額を消費枠として扱います。小額も0.000001円まで表示します。

台帳と `.initialized` 初期化記録を、**アプリと全ワーカーを終了してから**フォルダごとバックアップしてください。
初期化記録があるのにDBが消失・空になった場合は、新規DBを作って0円から再開せず停止します。
既存DBに管理テーブルが一部欠けている場合や不整合がある場合も、自動再作成しません。
初期化記録を含む全履歴を人が削除した場合や、古いバックアップを戻した場合の実請求を復元することはできません。提供元の明細と照合し、安易に新しい台帳で再開しないでください。

## 予約と単一実行

`BudgetLedger.reserve()` はSQLiteの `BEGIN IMMEDIATE` によって残額検査と最大料金の予約を直列化します。
同じrequest_idは同じ内容なら `already_*` を返し、別の内容・見積根拠なら拒否します。
`BudgetedExecutor.run()` は `reserved` を新規取得した場合だけ、永続的な実行権を取得してから**1回だけ**コールバックを実行します。
二重起動・同時処理・アプリ再起動後に同じIDを再実行しません。結果キャッシュの照会はTTS側の仕事で、再送の代替ではありません。

- 実行権取得後は `cancel_not_sent()` と実行権なしの第三者精算を拒否します。
- 実行権を取得していない、確実に未送信の予約だけ `cancel_not_sent()` で解除できます。
- タイムアウト・中断・利用量欠落・不正な実績・精算失敗は `uncertain` とし、最大予約額を保持します。
- 不明状態の記録自体が失敗しても、既存の予約と実行権は残り、自動解除・再送しません。
- `uncertain` または未確認の見積超過が1件でもある間は、全作品の新規実行を停止します。
- `dry_run()` は予約・ネットワーク通信なし。providerを指定すればプロバイダー枠も確認できます。試算は予約ではなく、実行直前に再検査します。

旧版の台帳は元のリクエスト行を残して、設定・見積・実行権・超過確認の管理テーブルを追加します。
旧版の `reserved` は送信済みか区別できないため、保全用の実行権を追加して手動確認を要求します。金額・旧行を勝手にゼロや精算済みにしません。

## 料金表と最大利用量の契約

金額文字列だけを `run(quote_jpy=...)` に渡しても実行できません。アダプターは次を渡します。

- `PriceRates`：公式の公開HTTPS URL、確認日（YYYY-MM-DD）、provider、model、USD建ての入力／出力トークン単価・件数単価、件数の単位、税の適用範囲。
- `PriceQuote`：明示した想定最大為替 `fx_jpy_per_usd`、税の最大係数 `tax_multiplier`、安全係数 `safety_multiplier`（1以上）。為替の暗黙初期値はありません。
- `usage_limits`：最大入力トークン、最大出力トークン、最大件数、必要なら追加最大USD、**retry_limit=0**。
- 金額の単位はすべて明記し、未確認の費目があるモデルは接続しません。料金確認の有効期間は最大30日（短く設定可能）。未来日・期限切れ・モデル不一致は停止します。

見積は入力・出力・件数・追加料金を合算し、為替・税・安全係数を掛け、1円の100万分の1単位へ切り上げます。
`quote_jpy` を追加指定する場合は、計算結果以上でなければ拒否します。設定と最大利用量は予約と一緒に保存します。
料金URLの公式性やモデルの存在を自動認定する機能はありません。今後の接続PRで公式資料を調べ、人間が料金表を登録する契約です。架空の本番モデル・単価を用意していません。

以下は**次のアダプター用の呼び出し契約（変数はアダプターが用意するため単独実行不可）**です。

```python
response = BudgetedExecutor(shared_ledger).run(
    project_id=project_id, request_id=stable_job_request_id,
    provider=verified_rates.provider, model=verified_rates.model, kind="tts",
    price_quote=PriceQuote(verified_rates, fx_jpy_per_usd=verified_max_fx,
                           tax_multiplier=verified_tax_factor),
    usage_limits={"max_input_tokens": max_input_tokens,
                  "max_output_tokens": max_output_tokens,
                  "max_items": max_billable_items, "retry_limit": 0},
    operation=single_provider_call_with_sdk_retries_disabled,
    actual_charge_jpy=charge_from_verified_usage,
)
```

SDKの自動再試行も次のアダプターで明示的に無効化します。コールバック内部でのモデル変更・多重送信はこの基盤から検知できません。
出力上限や請求の最大値を制限できないAPIは接続禁止です。安全係数は提供元の請求保証の代わりにはなりません。

## 月替わりと手動確認

精算済み金額は請求確定月、未確定予約は予約月に関係なく当月枠にも算入します。
月替わりを理由に未確定請求の枠を解放しません。作品枠はすべての月を累計します。
`settle(..., charged_at=...)` の既定は精算時点の日本時間です。明細の確定日時が分かる場合だけ、信頼できる日時を明示します。
超過した実績は予約額に丸めず、その額を記録します。500円を超えた作品には、新規処理を許可しません。

手動照合を行う運用担当者だけが、以下を実行します。バックグラウンド再試行から呼ばないでください。

- 不明な請求が実際には存在しなかった：`reconcile_no_charge(id, confirmed_no_charge=True)`。
- 不明な請求の実額を確認した：`settle(id, actual_jpy=確認額, confirmed_charge=True)`。
- 旧版からの未処理予約：まず `mark_uncertain(id)` にし、明細照合後に上記のいずれか。
- 見積超過の原因と修正後の見積を確認した：`acknowledge_overrun(id, confirmed_reviewed=True, reason=確認理由)`。元の実額は消しません。

この画面は表示専用で、請求を一括解除するボタンはありません。
操作を誤ると実際の請求をアプリから知ることはできないため、提供元の予算・通知設定も併用します。

## テスト・次の段階

```bash
python -m pytest -q
python -m pytest -q tests/test_video_budget.py tests/test_budget_review.py tests/test_budget_lifecycle.py tests/test_budget_app.py tests/test_video_budget_ui.py tests/test_video_prereqs.py
```

価格はテスト専用の架空データ、APIはローカル関数だけで検証します。新しい必須依存はありません。
修正前後の結果は [PR #2検証記録](../verification/pr2_budget_review.md)、TTS／FFmpegの前提と契約は [次タスクの依存関係](video-next-phases.md) を参照してください。
