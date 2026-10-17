"""APIなしの動作確認用返答。依頼ID・版・人物IDは現在の依頼へ合わせる。"""

from manga.artwork_models import CHARACTER_ALL_FIELDS, direction_fields
from manga.assistant_prompts import response_template


def sample_response(request):
    payload = response_template(request)
    kind, artwork = request["kind"], request["context"]["artwork"]
    if kind == "characters":
        fields = dict(name="デモ人物A", age="不明", gender="未設定", hairstyle="短い髪", hair_color="銀色", eye_color="青", height="未設定",
                      body_type="細身", clothing="紺色の上着", personality="好奇心が強い", features="星の髪飾り", role="主人公候補", visual_identity="銀髪と星の髪飾りで識別")
        payload["items"][0].update(fields=fields, provenance={key: dict(status="AI提案", evidence="動作確認用の架空の提案") for key in CHARACTER_ALL_FIELDS}, questions=["デモ設定です。実際の人物設定として採用する前に修正してください。"])
    elif kind in ("directions", "improvement"):
        for item in payload["items"]:
            entry = artwork["panels"].get(f"{item['page']}:{item['panel']}") or {}
            current = entry.get("direction")
            item["fields"] = direction_fields(current or {})
            item["fields"].update(location=item["fields"]["location"] or "舞台は要確認", expression="静かな決意（デモ）", pose="前を向く（デモ）",
                                   camera_angle="目線の高さ", composition="余白を生かして視線を誘導", background="物語の舞台に合う背景・詳細要確認",
                                   lighting="柔らかな光", color_palette="保存済みの作風と色彩を維持", dramatic_role="感情を伝える", continuity="前後のコマの立ち位置・衣装を確認")
            item["reason"] = "動作確認用。視線と感情の流れを整理する提案です。"
            item["questions"] = ["人物・場所・演出を実際のストーリーと照合してください。"]
    elif kind == "references":
        payload["items"][0].update(fields=dict(title="銀河鉄道の夜", medium="小説", genre="幻想文学", reason="旅を通して問いを深める技法の検討候補", techniques="旅の途中で問いを積み重ねる構成（要確認）", scene_summary="", avoid="固有の人物・台詞・展開の再現", source_url="", notes="動作確認用の候補"),
                                   staging="場面の移動で感情を変化させる技法を検討", application="自作独自の場所と出来事で主人公の選択を描く", basis="サンプルであり作品本文・各場面は未確認。ご自身で確認してください。", questions=["原作の内容と提案した技法の対応を確認してください。"])
    else:
        payload["summary"] = "動作確認用の主観的なサンプル評価です。実際のAI評価ではありません。"
        for score in payload["scores"].values():
            score.update(score=6, good="設定の枠組みが整理されている", problem="感情の流れは要確認", reason="デモとしての仮評価", suggestion="前後のコマの視線をそろえる", targets=request["targets"][:1])
    return payload
