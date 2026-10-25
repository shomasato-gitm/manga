# Phase 9：料金根拠・予算・安全性

確認日：2026-10-26。公開料金の出典は[候補比較](phase9-provider-comparison.md)に記載。phase9-provider-evidence.jsonは調査専用で実行用PriceQuoteではない。旧Gemini readiness/manifestへ自動転記しない。NETWORK_VERIFIED=False、本番manifest、料金根拠確認、予算ガードを解除・迂回しない。

## 実送信の条件（将来設計）

1. 正式モデル/固定版、地域、権限、入出力parameter、商用条件、送信範囲を確認。無料APIでも実際のtier/枠/規約を確認する。
2. 最大input/output/reasoning/thinking、画像・参照画像、追加課金単位を揃え、不明があれば送信不可。平均量やUTF-8 bytesの制限だけをtoken上界にしない。
3. 公式URL・確認日・サービス別（Developer API/Vertex等）・モデル・単位・tier/region・為替・税・安全係数・有効期限（現行1〜30日）を料金カードに記録。
4. 最大円額と共有予算残額を表示。作品は全期間累計500円、全作品月間5,000円。予約・不明請求も消費に含め、DB再作成/切替/初期化で残高を復活させない。
5. 課金は既定OFF。今回一つのrequest_id・凍結payload・素材hash・モデル・上限へ個別承認。素材変更で承認失効。
6. 承認を永続的に一度だけ消費。BudgetedExecutorの予約/execution claimとadapterのclaim_provider_callを通す。自動再試行・モデル変更・品質昇格・追加tool・画像一括生成は禁止。
7. usage・model・request ID・形式/件数/寸法/SHAを検証して既存Storeへ候補保存。usageから費用記録。採用・権利判断は別の人間操作。
8. timeout・usage欠損/未知・見積超過・保存不明はfail-closed。予約保持、照合まで再送禁止。応答保存済みなら同IDで保存だけ復旧。

プロバイダーの残高/spend limit/rate limitとアプリ予算は別防御。別アプリ利用、最終為替、税、請求遅延はアプリだけでは統制できない。無料枠不明・枯渇時は停止して手動へ戻し、有料へ自動移行しない。

## 1作品の参考費用（最大見積ではない）

仮定：8ページ24コマ、再生成なし。textは企画/脚本/評価を合わせて**合計入力100,000・出力20,000token（思考含む）**、音声3,000請求文字、画像24枚。150円/USD・税1.10倍は計算用仮定で、実際の為替/個別税率を確認した主張ではない。機材・電力・ChatGPT契約・通信・保存費を除く。

| 構成・項目 | 算式 | 参考円額 |
| --- | --- | ---: |
| 手動text/画像＋ローカル音声 | アプリの課金APIなし | API分0（local voiceは9-B後、現行mock） |
| Gemini Flash-Liteの上記text、有料単価比較 | (0.10×0.1+0.40×0.02)×150×1.10 | 2.97 |
| gpt-5-miniの上記text、提供継続未確認 | (0.25×0.1+2.00×0.02)×150×1.10 | 10.725 |
| Polly Standard音声のみ | 4×3000/1,000,000×150×1.10 | 1.98 |
| Polly Neural音声のみ | 16×3000/1,000,000×150×1.10 | 7.92 |
| Gemini画像24枚の画像出力部分だけ | 24×0.0336×150×1.10 | 133.056 |
| OpenAI mini low24枚の画像出力部分だけ、提供継続未確認 | 24×0.005×150×1.10 | 19.80 |

手動画像＋local voice＋上記Gemini有料textなら参考約3円、条件内の無料枠ならAPI分0円。ただしGemini text/local voiceは今回未実装で品質も未検証。OpenAI text＋Polly Standardなら同条件で約12.71円だが、提供状態等の確認まで実行不可。

**画像API込みの総額・最大額は未確定。** 入力prompt・参照画像・text/thinking・再生成・失敗処理は上記出力額に含まれない。「24枚が予算内」の承認根拠に使わず、一括生成も禁止する。

最大見積は参考消費量でなく、公式に強制できる一回ごとの上限から算出する。必要ならモデルの入力幅全体を上界に予約し、出力/思考枠・税・安全係数（現行既定1.25）を含める。為替・税・追加単位が未確認ならPriceQuoteを作らない。キャッシュ割引や一時creditは引かない。Pollyはタグを含むSSML UTF-8 bytesによる保守的予約を維持し、返却の請求文字数で記録する。

## 秘密と送信範囲

- キーは環境変数/Secretsのみ。認証header、URL認証query、エラー生本文もGit/JSON/log/画面/スクリーンショットに保存しない。診断は存在booleanのみで、値・長さ・一部・hashを出さない。
- Textは今回選んだ脚本・企画・人物・参考情報・system promptを事前表示。他作品/未選択履歴/個人情報/budget DBを送らない。無料枠のデータ利用条件に同意できなければ手動へ。
- Imageは脚本版・人物/バイブル・指示・prompt、参照各ID/SHA/寸法/枚数と送信先を確認。参照画像の外部送信に各件の同意を要求。
- Voiceは発話の読みと声設定だけ。人物画像/全作品は送らず、外部Pollyとlocal VOICEVOXを区別。

## VOICEVOXのローカル境界

9-Aのvoicevox_origin_allowedは文字列判定のみで接続しない。許可候補は http://127.0.0.1:50021 と http://[::1]:50021 の二つだけ。

9-Bも初期はこのallowlistを固定する。localhostを含むDNS名、別表記IP、127/8全域、LAN、任意port、userinfo/query/fragment/path付きoriginを拒否。コードが固定endpointだけ付加する。redirect追跡を禁止し、local接続では環境HTTP proxyへ発話を渡さない。cloud向けproxy設定と分離する。

/version、/engine_manifest、/speakers等も本人がローカル確認を選んだ場合だけ読む。サイズ/JSON/versionを検査。audio_queryとsynthesisの二つのlocal処理を一つの音声計画として明示する（課金APIの1承認1送信と異なる）。再試行、未知core/styleへの置換、有料TTSへのfallback禁止。

停止/非対応時は未完了のまま保持し、本人が無料mockか将来の手動WAVを選ぶ。同portに別プロセスもあり得るためmetadata/規約/version確認を省略しない。cloudのlocalhostはユーザーのMacではない。無断トンネルや外部公開、macOS12向け未確認の古いバイナリの自動取得は対象外。

## 調査記録と診断

調査JSONに正式ID・参考料金・公式URL・日付・取得状態を保存。web閲覧の取得バイト列SHAは算出していない。未取得/不正/未来/期限切れ/最大額不足は接続不可。全要件をJSONでconfirmedに変えても9-Aは実行無効。

診断にtransport/予約/実行/保存メソッドはなく、既存停止フラグも変えない。既存アプリの有料設定を強制停止するツールでもない。本番の有効化は9-B/9-Cの個別レビュー・試験で判断する。
