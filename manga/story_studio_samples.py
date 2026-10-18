"""現在の依頼IDに対応する、API不要の動作確認用サンプル。創作の完成品質ではない。"""
import copy

from manga.story_studio_prompts import response_template


def sample_studio_response(request, score=7):
    payload = copy.deepcopy(response_template(request))
    if request['kind'] == 'concepts':
        conflicts = ['目的を隠した協力者と限られた時間', '共同体の規則と主人公の信念', '大切な記憶と未来の選択']
        endings = ['対話で新しい約束を選ぶ', '失敗を認め仲間へ役割を託す', '別れを受け入れて一歩を踏み出す']
        turns = ['協力者の本当の願いが分かる', '規則を守る側にも恐れがあると知る', '失ったと思った記憶が行動に残る']
        for index, concept in enumerate(payload['concepts']):
            concept.update(title=f'物語の選択 {index+1}', logline=f'主人公が選択{index+1}を通じて目的に向き合う。',
                           synopsis=f'葛藤{index+1}から転換{index+1}を経て、結末{index+1}に至る。',
                           protagonist_goal='大切な相手に自分の思いを届ける', conflict=conflicts[index],
                           turning_point=turns[index], ending_direction=endings[index],
                           originality='固有の人物・舞台・セリフを再現しない創作設定', concerns='演出の説得力は人間が確認する')
            for item in concept['technique_uses']:
                item['application'] = f'案{index+1}の感情の変化に抽象技法を応用する'
    elif request['kind'] == 'evaluation':
        for item in payload['evaluation']['criteria'].values():
            item.update(score=score, good_points='主人公の目的が明確', problems='転換点の説明を短くする',
                        reason='感情の変化を読み取りやすくする', suggestion='動作で決断を示す')
        payload['evaluation']['summary'] = '動作を通じて感情を伝えると読みやすくなる。'
        for item in payload['reference_usage']:
            item.update(status='活用できている', reason='情報開示が主人公の決断につながっている')
    elif request['kind'] == 'detail':
        story = payload['story']
        story['summary'] = request['context']['concept']['synopsis']
        for page in story['pages']:
            page['summary'] = f"主人公が目的に近づく第{page['page']}場面。"
            for panel in page['panels']:
                panel.update(description=f"{page['page']}ページ{panel['panel']}コマ。主人公が相手に向き直る。",
                             dialogue='主人公：自分の言葉で伝えよう。', narration='')
    else:
        options = request['options']
        target = options['targets'][0] if options['targets'] else dict(page=1, panel=1)
        panel = payload['story']['pages'][target['page']-1]['panels'][(target['panel'] or 1)-1]
        panel['description'] += ' 主人公は視線を上げ、自分から一歩踏み出す。'
    return payload
