# Phase 2-C → 2-D：人物・画像工程の連携契約

## 正本と識別子

- 脚本は既存StoryStoreの正式版UUID。候補story_aiのUUIDを混在させない。生成・採用前に最新正式版の脚本確定を検証する。
- 人物は既存ArtworkStoreのUUID。プロフィール・参照画像・primary_reference_idの正本はartwork.json / schema_version:1。既存人物を別UUIDで作り直さない。
- 人物提案はAssistantStore / schema_version:1のkind:characters。request.character_aiはモックの抽出根拠・照合候補・送信0件を記録する任意メタデータ。原案・編集・採用の既存履歴を維持する。
- バイブルはcharacter_ai.json / format:manga-character-ai / schema_version:1のbibles[]。人物UUID、正式脚本版、プロフィールと参照のスナップショット、ロック、別名、対象コマ付き例外、根拠、元の採用IDを保存する。

## 呼び出し例

```python
from manga.character_ai_service import CharacterAIService

service = CharacterAIService(projects_directory, project_id)
package = service.prompt('panel', page=1, panel=2)
# package: kind, character_id, page, panel, story_version_id, source_token,
# bible_ids, reference_images, warnings, prompt
```

この関数は画像を生成せず、入力を変更しない。既存panel_image_prompt / character_image_promptへ固定設定・承認済み例外・参照ID・SHAを補足する。
例外はこのコマのコピーへだけ適用する。基本プロフィール・他のコマ・元画像を変更しない。
source_tokenは脚本・作画・人物・バイブル・参照のファイル状態等を結び付ける。実行直前に更新を検証する。古いバイブル・明示的な固定矛盾・不正な人物IDを無視して画像生成しない。
画像本体は自動添付されない。reference_imagesから安全なパスとIDを照合し、人間が必要な画像を添付する。参照未登録・欠損は未完了／警告であり、外見一致の合格ではない。

## 例外とコマ別指定

場面例外は`{targets:[{page:1,panel:2}], changes:{clothing:"体操服"}, reason:"体育の回想"}`の配列。バイブルの保存時に対象・変更項目・理由・非競合を検証し、人間の承認を記録する。
`observations[]`は、コマの人物に明示した検査用テキスト設定の履歴。固定設定を書き換える命令ではない。最新正式版と対象コマの最新記録だけを使用し、過去版を自動適用しない。
検査は設定・ID・版・ハッシュ・承認状況に基づく。自然文全体や顔の画像認識は未実装。将来画像解析を加える場合も、今回の静的検査と結果を区別する。

## 互換性と予算

story_history.json、artwork.json、composition.json、editorial.json、voice.json／voice-timeline、videoの既存形式は変更しない。
既存PNG・参照画像・候補画像・選択履歴・音声を削除せず、元の設定と違う場合は再確認する。キャッシュを人物UUIDの変更で回避しない。
人物提案の採用・復旧はAssistantStoreの既存ジャーナルを再利用する。別編集や壊れた復旧記録がある場合は強制上書きしない。
character_ai.jsonとcharacter_ai/の初期化記録を一緒にバックアップする。今回のGitHub自動同期対象を拡大していないため、バイブルの持ち運びは別途確認が必要。

Phase 2-Dで有料接続する前に、利用可能な公式モデル・日本語・画像参照・料金・商用条件・最大請求量を再確認する。
本Phaseはモック専用で有料アダプターや料金の推定値を追加していない。次工程の全送信は共通BudgetedExecutorを通し、永続request_id、個別承認、機能フラグ、秘密鍵、上限予約、請求不明時の停止を実装する。
企画・脚本・音声等と同じ作品UUID・同じ予算DBを使い、生涯500円／全作品月間5,000円の残額を共有する。人物用・画像用に別台帳へ切り替えて残額をリセットしない。
