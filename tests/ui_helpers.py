"""旧機能の回帰シナリオを、新しい実アプリのメニューを経由して操作する。"""

def go(app, menu, section=None):
    from manga.navigation import resolve_route
    menu, section = resolve_route(menu, section)
    nav=next(r for r in app.radio if r.label=="制作メニュー")
    if nav.value != menu:nav.set_value(menu).run(timeout=20)
    if section:
        selector=next(s for s in app.selectbox if s.label=="作業を選択")
        if selector.value != section:selector.set_value(section).run(timeout=20)
    assert not app.exception


def route(label):
    if "キャラクター提案JSON" in label or "キャラクター一括生成の依頼" in label:return "キャラクター設定","AI一括生成・採用"
    if "参考作品候補JSON" in label or "参考作品候補の依頼" in label:return "ストーリー制作","参考作品のAI候補"
    if "作画指示提案JSON" in label or "作画指示一括生成の依頼" in label:return "作画指示・AI評価改善","AI一括生成・採用"
    if "作画改善JSON" in label or "作画改善の依頼" in label:return "作画指示・AI評価改善","AI改善"
    if "作画評価JSON" in label or "作画評価の依頼" in label:return "作画指示・AI評価改善","AI評価"
    if label in ("タイトル","ジャンル","あらすじ","ページ数","プロジェクトを保存"):return "プロジェクト管理",None
    if label.startswith("提案：") or label.startswith("採用候補：") or label in ("確認・編集する提案","この提案の編集を保存","選択した提案を採用"):return None
    if "ストーリー評価JSON" in label or "改善版ストーリーJSON" in label or label in ("評価JSONを検証して保存","改善方式","改善したい評価項目","改善するページ・コマ","今回の改善理由・メモ","改善版を検証して新しい版に保存","評価・改善する版"):return "ストーリー制作","ストーリー評価・改善"
    if "比較" in label or label in ("現在の脚本を確定","この版を採用（復元）","採用する版"):return "ストーリー制作","比較・脚本確定"
    if "復元するバージョン"==label or label=="このバージョンを復元":return "履歴・設定","ストーリー履歴"
    if "参考作品の分析JSON" in label or label in ("分析する参考作品","分析JSONを検証して保存"):return "ストーリー制作","参考作品の分析"
    if label in ("作品名","媒体","参考にしたいストーリー技法","参考作品を保存","編集する参考作品","この参考作品を一覧から削除する","参考作品を削除"):return "ストーリー制作","参考作品"
    if "ChatGPTのJSON" in label or label in ("取り込み方法","検証してストーリーを保存","ストーリー生成に使う参考作品（任意・3作品まで）"):return "ストーリー制作","生成・取り込み"
    if label=="編集するページ" or label=="このページを保存" or label=="このページの概要" or "コマ目の" in label:return "ストーリー制作","ページ編集"
    if label in ("名前","髪色","年齢","編集するキャラクター","キャラクターを保存","このキャラクターを削除") or label.startswith("「"):return "キャラクター設定","手動設定"
    if "参照画像" in label or label in ("生成する向き・種類","プロジェクトの作風","作風を保存","基準画像を選択"):return "画像制作・画像管理","基準画像・作風"
    if label in ("場所","表情","カメラ角度","登場キャラクター","このコマの作画指示を保存","作画指示のページ","作画指示のコマ"):return "作画指示・AI評価改善","コマごとの編集"
    if "候補画像" in label or label in ("画像を管理するコマ","採用候補を選ぶ","選んだ候補を採用","過去のコマ"):return "画像制作・画像管理","コマ画像・採用一覧"
    if "フォント" in label or label in ("ページの横幅（px）","ページ共通設定を保存","出力ファイルを生成","出力形式"):return "PNG・PDF書き出し",None
    if "吹き出し" in label or "コマ割り" in label or "レイアウト" in label or "コマ：" in label or label in ("コマの配置を手動調整","文字の方向","追加する文章の種類","ページをプレビュー・再描画"):return "漫画ページ編集",None
    return None


def element(app,kind,label):
    found=next((x for x in getattr(app,kind) if x.label==label),None)
    if found:return found
    target=route(label)
    if target:go(app,*target)
    old_page_labels=("レイアウトテンプレート","コマの配置を手動調整","このページのコマ割りを保存","この吹き出し専用の文章を使う","吹き出しの本文","吹き出しを保存","編集する吹き出し","追加する文章の種類","吹き出しを追加","文字の方向")
    if label in old_page_labels or label.startswith("吹き出し：") or "コマ：" in label:
        method=next((r for r in app.radio if r.label=="編集方法"),None)
        if method and method.value!="従来の数値編集":method.set_value("従来の数値編集").run(timeout=20)
    from manga.artwork_models import DIRECTION_FIELDS
    if label in (*DIRECTION_FIELDS.values(), "登場キャラクター", "このコマの作画指示を保存"):
        editing=next((c for c in app.checkbox if c.label=="作画指示を編集"),None)
        if editing and not editing.value:editing.check().run(timeout=20)
    if kind=="text_area" and label.endswith("を貼り付け"):
        radio_label="取り込み方法" if label=="ChatGPTのJSONを貼り付け" else label[:-len("を貼り付け")]+"の入力方法"
        r=next(r for r in app.radio if r.label==radio_label)
        r.set_value("貼り付け").run(timeout=20)
    return next(x for x in getattr(app,kind) if x.label==label)


def button(app,label):
    element(app,"button",label).click().run(timeout=20)
    assert not app.exception
