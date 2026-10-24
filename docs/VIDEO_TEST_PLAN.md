# Phase 3のテスト方針

実作品・本番キー・課金APIを使用しません。tmp_path内の専用作品・別DB・自作PCMを使用します。
『君が忘れた、最後の約束』を想定した8ページ24コマ・3人物＋ナレーターのfixtureで検証します。
モックとプロバイダーのfake transportは日本語音声品質の実証ではありません。

| ファイル | 検証範囲 |
| --- | --- |
| tests/test_voice_core.py | 抽出、曖昧話者、NFC/NFD、長文の手動修正、キャッシュ、変更発話のみ生成、保存競合、欠損、履歴復元、実尺・字幕、タイムライン構造 |
| tests/test_voice_paid.py | BudgetedExecutor、500円枠、明示承認、確定脚本、期限切れ・不正料金、同時生成、タイムアウト・利用量欠落・超過、予約保持、保存前後の失敗、同一ジョブ復旧、台帳差替拒否 |
| tests/test_voice_transport.py | SigV4送信内容と応答処理、サイズ上限、期限、読込失敗、リダイレクト拒否、WAV切断、ffprobe引数と不一致 |
| tests/test_voice_app.py | Streamlit全アプリから新画面へ移動、モック生成・試聴・確認・字幕、JSONファイル初期値、入力保持、旧候補採用 |
| scripts/verify_voice.py | 実WAV/SRT/JSONをAPIなしで新規フォルダへ作成。5件生成→5件キャッシュ→変更1件だけ生成、旧音声保持 |

新規テストは実ネットワーク接続を拒否し、AWSのsendをメモリ内応答に置き換えます。
既存全pytestには予算の月間5,000円・並行予約・復旧と、漫画制作・画像・ページ編集・GitHub同期・既存Streamlit画面の回帰テストを含みます。
GitHub ActionsはPython 3.11／3.12で全pytestを実行し、JUnitを保存します。

```bash
python -m pytest -q --junitxml=test-results/pytest.xml
python scripts/verify_voice.py --output /tmp/manga-voice-unused-directory
```

実測した件数・時間・変更前後は [検証記録](../verification/voice_phase3.md) に記載します。
Intel Mac実機、Polly実音声、実際のAWS請求との照合は未実行です。承認なしで「結合テスト成功」と扱いません。
次Phaseでは実画像・WAVからFFmpegでMP4を作り、codec・寸法・尺・字幕・権利確認・中断復旧を追加検証します。
