# 企画・脚本から人物・画像・音声・動画への連携

## 正式な入口

次の工程は **StoryStore.load_history()の正式版ID** を使います。AI候補のUUIDを正式版として扱わないでください。
採用後の脚本は既存schema_version:1のtitle・summary・pages・panels・description・dialogue・narrationのままです。
採用は新しい履歴の追記であり、脚本確定は従来の人間の操作です。候補を生成・評価しただけでは後工程へ自動送信しません。

| データ | 対応関係 |
| --- | --- |
| story_ai.concept_batches / selections | 企画セット、各企画UUID、選択履歴。選ばなかった案を維持 |
| story_ai.candidates | 候補UUID、生成request_id、企画UUID、改善回数、親候補、既存形式のstory、page_hooks、変更理由・実差分 |
| story_ai.evaluations | 候補UUIDと一致するstory_version_idを持つ8項目評価。総合点はローカル再計算 |
| story_ai.adoptions | 候補UUID→実際のStoryStore版UUID、候補評価UUID→editorial評価UUIDの対応 |
| editorial.evaluations | 採用した脚本の実StoryStore版IDへ評価を対応。source_storyも一致させる |

専用メタデータはformat:manga-story-ai / schema_version:1で、旧story schemaとは別です。
`page_hooks` は全ページのpage/hook配列で、旧JSONへ新必須項目を追加しません。
API Structured Outputsは企画、story+page_hooks+change_reason、既存評価JSONを使い、受信後も既存normalize_story / normalize_evaluationを通します。

## 人物・画像工程

- 既存ArtworkStoreの人物UUID・設定・参照画像は書き換えません。テキスト設定を脚本生成・評価・改善へ渡します。画像本体を見たと主張させません。
- 新規人物を提案しても、自動登録・既存人物IDの変更は行いません。採用済み脚本から既存「AI一括生成・採用」または次の人物生成工程へ渡し、重複と設定を確認します。
- 作画指示・コマ画像は正式StoryStore版IDとページ／コマ番号へ結び付けます。候補IDとの混在を避けます。
- 改善後の再採用では、以前の指示・画像を残したまま版の不一致を再確認します。既存品質チェックを使い、画像を自動再生成・再採用しません。

## 音声・動画工程

正式脚本を人間が確定した後、既存音声工程が話者と人物IDを照合します。変更セリフだけの音声キャッシュ規則は維持します。
voice-timeline schema_version:1 → video.jsonの独立計画 → 3形式MP4というPhase 3/4の契約も変更しません。
この機能からTTS・画像・動画生成を自動実行する呼び出しは追加しません。

## 共通予算と競合

将来の人物・画像APIもopen_budget(projects_directory)とBudgetedExecutorを使い、同じ作品UUIDに対する累計500円／全作品月間5,000円へ合算します。
企画・脚本に使った分を別予算へ移したり、採用・月替わり・新しいジョブで作品枠をリセットしたりしません。
同じrequest_idの再送を拒否し、モデル・料金・依頼上限・承認ハッシュ・応答・請求状態を保持します。
正式採用はプロジェクト／脚本／人物／評価／AIのロックと前後記録で保護します。競合時は停止し、履歴の削除・強制上書きで解消しません。
