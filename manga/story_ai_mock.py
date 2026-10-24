"""無料の決定的fixture。実モデルの日本語脚本品質を証明するものではない。"""
import copy
import json

from manga.editorial_prompts import evaluation_template
from manga.models import Project
from manga.prompts import story_template


class MockStoryAI:
    def generate(self,request):
        kind=request['kind'];config=request['settings'];cycle=request['cycle']
        if kind=='concepts':
            patterns=[('交換','互いの手紙を交換して誤解をほどく','約束を守り別々の道へ進む'),('探索','失くした約束の場所を仲間と探す','探していた場所が現在の教室だと気付く'),
                ('選択','約束と新しい夢の間で決断する','自分の選択で新しい約束を結ぶ'),('逆転','助ける側だった主人公が助けを求める','弱さを共有して対等になる'),('再会','すれ違った二人が期限内の再会を目指す','再会せずとも届いた想いが次の一歩になる')]
            ideas=[]
            for i,(name,conflict,ending) in enumerate(patterns[:config['concept_count']],1):
                ideas.append(dict(title=f'{name}の約束・案{i}',logline=f'{config["theme"]}を巡る{name}の物語',synopsis=conflict+'。'+ending,
                    protagonist_goal='自分の言葉で約束を伝える',characters='主人公と大切な相手',conflict=conflict,turning_point='序盤の小さな約束の意味が変わる',ending=ending,
                    originality='日常の小道具を使った独立した設定',risks='確認用のひな形です。物語の魅力は人間が確認してください。'))
            payload=dict(ideas=ideas)
        elif kind=='evaluation':
            payload=evaluation_template(request['candidate']['id'])
            for item in payload['criteria'].values():item.update(score=5.5+.5*cycle,good_points='人物の目的をコマで示している',problems='動機や伏線をさらに明確にできる',reason='読者の理解を助ける',suggestion='小道具と選択の因果を結ぶ')
            payload['summary']=f'確認用モックの評価・改善{cycle}回。主観点の品質保証ではありません。'
        else:
            if kind=='draft':
                project=Project.from_dict(request['context']['project']);project.page_count=config['page_count']
                story=story_template(project);story['title']=request['concept']['title'];story['summary']=request['concept']['synopsis']
                names=[c['name'] for c in request['context']['characters']][:2] or ['主人公','友人']
                if len(names)==1:names.append('友人')
                for p in story['pages']:
                    p['summary']=f"第{p['page']}ページ。目的と選択を進める。"
                    for panel in p['panels']:
                        panel.update(description=f"夕暮れの教室。{names[0]}が手紙を持ち、{names[1]}の返答を待つ。",dialogue=f'{names[0]}：「約束を、伝えたい。」',narration='')
                    p['panels'][-1]['description']+=' 小道具の意味を問い、次へつなぐ。'
                story['pages'][-1]['panels'][-1]['dialogue']=f'{names[0]}：「また、自分の言葉で伝えるよ。」'
            else:
                story=copy.deepcopy(request['candidate']['story']);story['pages'][0]['panels'][0]['description']+=f' 改善{cycle}：手紙を握る理由を表情と動作で明確にする。'
            payload=dict(story=story,page_hooks=[dict(page=p['page'],hook='選択の結果を問い、次ページへつなぐ' if p['page']<len(story['pages']) else '約束の意味を回収して余韻を残す') for p in story['pages']],change_reason='初稿' if kind=='draft' else f'改善{cycle}：動機と伏線の因果を明確化')
        return dict(text=json.dumps(payload,ensure_ascii=False),status='completed',usage=dict(input_tokens=1,output_tokens=1),model='mock',response_id='mock')
