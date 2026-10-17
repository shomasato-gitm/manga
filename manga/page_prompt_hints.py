"""保存したコマ割りの比率を、手動の作画プロンプトへ伝える。画像解析は行わない。"""
from manga.page_layout import pixel_frames


def layout_hint(page,composition,panel):
    saved=composition["pages"].get(str(page["page"]))
    existing=bool(saved and len(saved["source_page"]["panels"])==len(page["panels"]))
    config=saved if existing else dict(template="hero_top",frames=None)
    origin="保存済みコマ割りの想定です。" if existing else "未保存の標準レイアウトです。編集後は比率が変わる場合があります。"
    frame=pixel_frames(page,config,composition["settings"])[panel-1]
    border=round(composition["settings"]["width"]*composition["settings"]["border"]/100)
    width,height=max(1,frame["width"]-2*border),max(1,frame["height"]-2*border)
    return (f"想定アスペクト比：横 {width} : 縦 {height}（約{width/height:.2f}:1）。{origin}\n"
            "人物配置は作画指示の構図を優先し、顔・頭髪・手・重要な小道具を外周ぎりぎりに置かないでください。\n"
            "吹き出しは後で配置します。可能な範囲で右上・左上の縁に背景の余地を設け、重要な対象をトリミングで失わない構図にしてください。文字・吹き出し自体は描かないでください。")
