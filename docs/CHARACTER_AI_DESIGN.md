# Phase 2-C：調査と実装方針（2026-10-24）

PR #7は2026-10-24 08:18:19 UTCにmainへマージ済み。最新main `fb47a2840d245595c4be23b9680edfa64ce815b3`から`feature/ai-character-consistency`を作成しました。実装前に既存コードを調査し、以下の分担にします。

| 既存を再利用する部分 | 今回の拡張 |
| --- | --- |
| AssistantStore、normalize_item / normalize_payload、production_prompt | 確定済み正式脚本を基点に、無料モックの抽出結果を同じ人物提案形式へ保存。通常のAI一括生成・採用にも残す |
| apply_items、differences、commit_adoption、resume | フィールド部分採用、人物の明示同定、固定項目のロック確認を追加。人物UUIDの採番・既存参照画像保持・2ファイルの保存復旧は既存処理 |
| ArtworkStore、人物UUID、references / primary_reference_id | バイブルからIDとファイルハッシュを参照。画像の生成・再採用・自動変更は行わない |
| split_dialogue、name_key | 明確な話者・登録人物名の根拠抽出、NFKC・空白・敬称・名字／名前の候補照合。候補一致は本人の断定ではない |
| character_image_prompt / panel_image_prompt | 承認済み固定設定・場面例外・参照画像IDを補足。矛盾や古い版を解決してから出力 |
| 6工程の遅延ルーター、既存UIランタイム | 「キャラクター・作風」へ任意の制作画面を追加し、非表示画面では処理しない |
| 共通BudgetedExecutorと予算DB | 今回は課金アダプターを追加しない。モックに実APIの設定・機能フラグを与えても送信しない。既存の500円／5,000円台帳は維持 |

独立した`character_ai.json`には、バイブルの版、ロック、確認済み別名、シーン例外、コマ別テキスト設定、静的検査、プロンプト履歴だけを保存します。正式な人物プロフィールの正本は引き続き`artwork.json`です。

今回のモックは日本語の任意の自然文を理解するモデルではありません。話者ラベル、登録名、利用者が追加指定した名前、明示的な設定ラベルを根拠に抽出します。ナレーションは人物へ自動登録しません。不明話者・同名人物は確認待ちにし、未指定属性を事実として補完しません。新たな外見案を出す場合は「AI提案（モック）」と区別します。

生成・採用は最新の確定済み正式版だけに限定します。過去の正式版も選択・閲覧できますが、制作対象にする場合は従来の履歴から復元して再確認・確定します。候補UUIDや古い版の提案を現在の人物へ無断適用しません。

主な追加予定ファイルは`character_ai_extraction.py`、`character_ai_proposals.py`、`character_ai_models.py`、`character_ai_storage.py`、`character_ai_service.py`、`character_ai_checks.py`、`character_ai_prompts.py`、責務別UI、テスト、検証スクリプトです。既存人物／ストーリー／音声／動画／同期のJSON形式は変更しません。画像の外見を解析・保証せず、テキスト・ID・版・ファイルハッシュだけを検査します。
