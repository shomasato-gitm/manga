# Phase 2-D3 検証記録（2026-10-24）

ブランチ：`feature/gemini-image-readiness`。
PR #10マージ後のmain `6d3ca3c018d2c261a10f3aa7fff5150e086f1225` から分岐。
mainへの直接変更・自動マージは行っていない。

## 公式調査と判断

指定されたDeveloper pricing / image-generation / billing / generate-contentと、
モデル・rate-limits・termsはプロキシのCONNECT 403により取得できなかった。
ネットワーク権限の確認（許可）後も再試行したが同じ結果だった。
モデル自体の不存在やGoogle全体の障害とは判定していない。

Google公式SDK、Vertex Global価格、Google Cloud予算の公開資料をHTTP GETで再取得。
本文SHA・URL・取得状況・確認日・短い引用を
[日付付き記録](../examples/image_ai/gemini_readiness_20261024.json)へ保存した。
[調査結果と残る10条件](../docs/GEMINI_IMAGE_READINESS.md)を画面・CLIでも提示する。

**no-goを維持**。Vertex参考の出力のみ0.0336 USD/1K ≈5.91円（160円/USD・税1.10）は
Developer APIの最大総額ではない。入力・参照・画像出力・thinkingを含む最大課金量は未確認。
Google側のアラート・支出制限を絶対上限とみなさない。

## テスト

| 実行 | 成功 | 失敗 | スキップ | 時間 |
| --- | ---: | ---: | ---: | ---: |
| 変更前 Python 3.12.14 全pytest | 879 | 0 | 0 | 216.58秒 |
| 変更後 Python 3.11.17 全pytest | 913 | 0 | 0 | 224.42秒 |
| 変更後 Python 3.12.14 全pytest | 913 | 0 | 0 | 222.86秒 |
| 追加・既存の対象テスト（readiness・HTTPS境界・画像API・予算） | 142 | 0 | 0 | 31.72秒 |

コマンド：`python -m pytest -q --junitxml=...`、
`python -m pytest -q tests/test_gemini*.py tests/test_image_ai_live*.py tests/test_budget*.py`。
追加34件を含む。全件には既存Streamlit UI、画像・JSON・保存履歴・GitHub同期・音声・
PNG/PDF・実FFmpeg動画生成の回帰テストを含む。CIの3.11/3.12結果はドラフトPRへ記載する。

追加テストの確認内容：

- 取得できないDeveloper資料を確認済みとみなさず、SDK/Vertex/Cloud予算を課金根拠に代用しない。
- 記録の欠損・破損・重複・モデル違い・日付不整合・リンク・不正URL・誤った参考額で送信禁止。
- 旧boolean、環境フラグ、キーがあっても根拠不足を解除できない。
- 読取専用診断が作品・共有台帳へアクセスせず、キーの値を出力せず、既存診断ファイルも上書きしない。
- 1つの予算実行権で2回APIを呼べず、contextをコピーして並列化しても1回だけ。
- 予約後・送信前の元データ変更を検出してHTTPを停止。取得済み実行権の予約は保持し、同じIDを再送しない。
- 0入力、不自然な0画像出力、不正bool usageを無料失敗と仮定せず、請求不明を保持。
- 実HTTPSの入口をfake openerへ完全置換し、POST本文、固定URL、ヘッダーだけのキー、
  timeout、429/500、不正JSON、読取上限、proxy/redirect無効、再試行なし、予約保持を確認。
- AppTestで準備状況・未確認事項を表示し、作品ファイルを変更せず送信ボタンを無効のまま維持。

既存fakeの正常・失敗・不足予算・参照変更・同時実行・候補比較/採用・保存復旧も回帰実行した。
既存500円生涯/5,000円月間のBudgetedExecutor/台帳を維持し、独立した画像予算DBは作成しない。

## コマンドによる無料検証

```bash
python scripts/check_gemini_image_readiness.py --output /tmp/gemini-readiness-d3-20261024.json
python scripts/verify_image_ai_live_fake.py --output /tmp/gemini-image-d3-fake-20261024
```

前者は想定どおり終了コード2（no-go）。API・作品・予算へのアクセス0。
後者は新規の8ページ24コマfixtureから1枚のfake候補を生成し、未採用状態を確認してから
テストコードで明示レビュー・採用・保存復旧を実施した。
[request_id・usage・画像SHA・結果JSON](gemini_image_d3_fake_report.json)。

**外部画像API呼び出し0回、ネットワーク試行0回、実際のAPI費用0円。**
fake HTTPは1回。14.30円予約・5.9224円換算は架空カードの専用検証台帳上の値。
実作品・本物のAPIキー・既存の予算DBを使用していない。

## 残る制約

最大課金量、DeveloperモデルID・利用資格、1024×1024/1枚保証、参照・thinking・usageの
モデル別契約、商用・データ/SynthID条件の公式確認が必要。
資料を取得できる環境で根拠を揃え、別PRで能力判定・必要なプロトコル修正・料金カードをレビューする。
その後も少額1枚の別途明示承認・キー環境変数・個別参照同意・二段階確認が必要。

今回、実API・人物一致の画質・Intel Mac実機は未検証。
代替は従来の無料モック/手動画像取り込み。Vertexへの変更は別サービスの再設計と別PRで扱う。
旧JSON形式・人物UUID・基準/候補/採用画像・脚本/評価履歴・音声・動画を移行・削除・上書きしていない。
