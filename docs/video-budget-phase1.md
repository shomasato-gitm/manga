# 動画制作機能：Phase 0〜1 (API予算ガード)

既存の漫画制作機能を変更せず、独立モジュールに課金前予算制御を追加しました。
動画編集と外部AI APIの実接続は**まだ実装されていません**。
このPRによって課金が発生することはありません。

## 設定

- 1作品あたりの生涯API予算: 500円（既定値、変更可能）
- 全作品合算の月間API予算: 5,000円（日本時間の暦月、既定値）
- 追加でプロバイダー別の月間予算も設定可能
- 1作品の枠は月が変わってもリセットされない
- APIキー不要。価格表は組み込まず、事前確認済みの料金表を外から渡す
- システムの予算台帳は `data/runtime/budget.sqlite3` 等の永続領域に置く。Gitにコミットしない

## 呼び出しの例（ここではダミー応答でありネットワーク通信なし）

```python
from uuid import uuid4
from manga.budget import BudgetLedger, BudgetedExecutor
ledger = BudgetLedger("data/runtime/budget.sqlite3",
                      project_limit_jpy="500", monthly_limit_jpy="5000")
guard = BudgetedExecutor(ledger)
project_id, request_id = str(uuid4()), str(uuid4())
estimate = guard.dry_run(project_id=project_id, quote_jpy="20")
if estimate["possible_under_project_and_month"]:
    response = guard.run(
        project_id=project_id, request_id=request_id,
        provider="demo", model="mock-model", kind="tts",
        quote_jpy="20", operation=lambda: {"ok": True},
        actual_charge_jpy=lambda result: "12",
    )
```

実際のAPI接続時は `PriceRates` に公式URLと確認日を登録し、
`PriceQuote` で最大入力・最大出力・最大生成件数から**上限見積**を計算する。
価格表の欠落や0円見積での有料リクエストを禁止する。

## 振る舞い

- SQLiteの `BEGIN IMMEDIATE` で予約を直列化。同一作品・全作品月額・プロバイダーごとに残予算を判定
- 同じrequest_idは新規予約を作らず、**実行ゲートでは再送を拒否**
- API例外や使用量不明時は `uncertain` として最大予約額を保持
- **ネット送信前だと確定している時だけ** `cancel_not_sent` で予約解除可能
- 不明な請求はオペレータによる手動照合後だけ `reconcile_no_charge(..., confirmed_no_charge=True)`
- 利用額が上限見積を超えた際には超過額をそのまま記録し、追加の生成予約を停止
- `BudgetedExecutor.dry_run` は課金も予約も行わない
- provider SDK呼び出しを必ず `BudgetedExecutor.run` に統一する（Phase 2/3の受け入れ条件）
- **API提供者の最終請求額を厳密に500円以内に保証するものではない**。プロバイダー側の予算/アラートも併用

## 次に追加するもの

Phase 3: セリフ抽出、人物別TTS音声、WAVキャッシュ、字幕タイミング。
Phase 4: FFmpeg/FFprobeの3種類のMP4出力。先に既存画像で検証する。
Phase 2: テキスト・画像生成APIとの統合。画像の自動再生成回数を制限する。

## テスト

```bash
python -m pytest tests/test_video_budget.py tests/test_video_prereqs.py
# 既存機能の回帰確認
python -m pytest
```

Python 3.9〜3.12で利用できる標準ライブラリのみ使用します。
