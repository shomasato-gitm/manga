# Phase 2-D1の画像・音声・動画への契約

## 正本と候補を分ける

脚本は最新の正式StoryStore版UUIDと脚本確定記録、人物は既存ArtworkStoreの人物UUIDを使います。
CharacterAIService.promptを基点にバイブルUUID・対象例外・参照画像ID／SHA・作風・作画指示を取得します。
image_ai.json / format:manga-image-ai / schema_version:1は独立した計画・依頼・候補・レビュー・採用履歴です。
候補PNGは`image_ai/candidates/CANDIDATE_UUID.png`。この候補IDを音声・動画の正規画像IDと混在させません。

```python
from manga.image_ai_service import ImageAIService

images = ImageAIService(projects_directory, project_id)
plan = images.plan('panel', page=1, panel=2)
job = images.create_job(plan, provider='mock', expected_revision=images.store.load()['revision'])
candidate = images.run(job['id'])
# ここまでは正規Artwork不変。review(全目視項目)とadopt(明示承認)は別操作。
```

`plan`にはkind・人物／ページ／コマ・正式脚本版・material_token・prompt_sha256・参照ID／SHA・バイブルID・構図／フォーカス／余白を含みます。
material_tokenは対象に関係する企画・脚本・作画・人物・バイブル・参照状態・ページ配置を結び付けます。別コマの画像採用だけで全候補を古くしません。
参照は実行前にPNG・SHA・安全な保存先を確認し、実アダプターが存在しない今回はreferences_sent_to_external:falseです。手動Web操作の添付実績はアプリが確認したものではありません。

## 正規登録と復旧

採用時だけArtworkStore.add_reference／select_referenceまたはadd_panel_image／select_panel_imageを使います。
ImageAIのadoptions[]がcandidate_id→正規image_id、registered_sha256、review_id、正式脚本版とregistration_idを対応させます。
人物UUIDや既存画像IDは変更しません。同じ登録の保存再開・再選択では画像IDを再発行しません。
保存途中の画像欠損はArtworkStore.restore_registered_imageを使い、登録済みメタデータ・寸法・再開記録のSHAを照合して欠損PNGだけを復元します。存在する異なるファイルは上書きしません。
基準画像を変更した場合は既存バイブルを無断更新せず、人間が新しい版として確認します。旧PNG・候補・選択履歴を保持します。

## 既存音声・FFmpegへ

音声／動画が読む正本は引き続き`artwork.json`のselected_image_idと`images/panels/UUID.png`です。image_aiの未採用候補を自動で使いません。
既存voice_timeline.build_timelineはこのPNGのSHA・正式脚本版を記録し、voice_contract schema_version:1を維持します。
video_sources.snapshotは実ファイル・SHA・音声実尺と現在の採用状態を検査し、古いタイムラインなら新しい保存を求めます。
画像採用後に音声を無条件再生成せず、既存WAVキャッシュを再利用して必要なタイムラインだけ更新します。画像機能からTTS／動画の自動課金・生成を呼び出しません。

モックの正規original_nameは`image-ai-REGISTRATION_UUID-TEST_ONLY.png`です。音声タイムラインの警告へテスト画像である旨を追加し、動画にも警告を引き継ぎます。
手動画像は`-MANUAL.png`で、元の入力名・バイト列・出典の確認メモはImageAIへ保存します。アプリが実AI生成・ライセンス・顔一致を認定した状態にはしません。
今回の検証スクリプトは32字幕・4モック音声・24採用PNGを実際のvoice_contractとvideo_sourcesへ渡します。新しい有料画像APIの品質や実請求は試験していません。

## 有料APIは未接続

最大総請求量を確認できず、2-D2の実アダプターは未実装です。料金研究JSONは実行用PriceQuoteではありません。キー・フラグ・承認でロックを解除する経路はありません。
追加時は1社1モデルに限定し、公式上限・料金確認日・利用条件・為替・税・安全係数・明示同意・参照の送信許可を検証します。
共通BudgetedExecutor・同じ作品UUIDと永続DBで作品生涯500円／全作品月間5,000円へ合算し、タイムアウト・usage不明・保存失敗で有料再送や予約解除をしません。
今回のオフライン保存復旧を、有料APIへの再送として流用してはいけません。将来の送信実行権・usage・PNG受信記録・精算・保存だけの再開を別途試験します。
image_ai領域を既存GitHub同期へ自動追加していません。Artwork等の既存形式を保ち、画像候補・元入力を含む作品フォルダ全体と予算DBをバックアップします。
