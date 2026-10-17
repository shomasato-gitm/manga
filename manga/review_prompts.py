"""完成画像レビューの手動ChatGPT連携。文章だけの作画評価とは別に保存する。"""
import json
from uuid import uuid4

from manga.models import now_iso, validate_id
from manga.review_context import digest
from manga.review_models import target, text, integer, SEVERITIES, CORRECTION_TARGETS
from manga.review_checks import make_issue
from manga.story import load_json_payload


def new_request(context, targets, reference_ids, question='', *, include_pages=False):
    if not context.latest or not context.artwork or not context.composition or context.errors:
        raise ValueError('ストーリー・作画設定・ページ編集を読み込める状態にしてください。')
    if not targets or len(targets) > 24: raise ValueError('1〜24件のページまたはコマを選んでください。')
    seen = set()
    for item in targets:
        target(item, context.pages)
        key = (item['page'], item['panel'])
        if key in seen: raise ValueError('対象が重複しています。')
        seen.add(key)
    valid_refs = {i['id'] for c in context.artwork['characters'] if not c['deleted_at'] for i in c['references']}
    if not isinstance(reference_ids, list) or len(reference_ids) > 24 or len(set(reference_ids)) != len(reference_ids) or any(i not in valid_refs for i in reference_ids):
        raise ValueError('参照画像は現在のキャラクターから重複なく24枚以内で選択してください。')
    text(question, '確認したい観点', required=False)
    if type(include_pages) is not bool: raise ValueError('完成ページ添付の設定が不正です。')
    return dict(id=str(uuid4()), created_at=now_iso(), story_version_id=context.latest['id'], snapshot=context.snapshot,
                targets=targets, reference_ids=reference_ids, question=question, include_pages=include_pages)


def response_example(request):
    t = request['targets'][0]
    return dict(schema_version=1, request_id=request['id'], story_version_id=request['story_version_id'],
        issues=[dict(page=t['page'], panel=t['panel'], category='キャラクターの一貫性', message='確認できた問題の内容',
            severity='中', reason='添付画像から確認できた根拠。不明な場合は不明と明記', suggestion='具体的な改善案',
            target='image', needs_human_check=True)])


def review_prompt(project, context, request):
    pages = []
    for page in context.pages:
        targets = [t for t in request['targets'] if t['page'] == page['page']]
        if not targets: continue
        panels = [p for p in page['panels'] if any(t['panel'] is None or t['panel'] == p['panel'] for t in targets)]
        pages.append(dict(page=page['page'], summary=page['summary'], panels=[dict(p, direction=context.artwork['panels'].get(f"{page['page']}:{p['panel']}", {}).get('direction')) for p in panels]))
    characters = [{k: v for k, v in c.items() if k not in ('references', 'created_at', 'updated_at', 'deleted_at')} for c in context.artwork['characters'] if not c['deleted_at']]
    return '\n'.join([
        '完成漫画の画像・ページをレビューしてください。文章だけの作画評価ではありません。APIは使わず手動で依頼しています。',
        '添付した画像だけを根拠に、人物の外見・背景の連続性・感情・人体・コマ割り・吹き出しの隠れ・日本漫画の右→左の読み順を確認してください。',
        '見えない画像、添付されていない基準画像、判別困難な手指や文字について断定しないでください。不明・人間による確認が必要と明記してください。',
        'AI評価は主観的な提案であり品質を保証しません。既存画像や設定は変更しません。確認できた問題がない場合は issues: [] としてください。',
        '対象外のページ・コマを追加しないでください。target は image / direction / bubble / layout / story / settings。severity は 高 / 中 / 低。',
        f'作品：{project.title}\nジャンル：{project.genre}\nあらすじ：{project.synopsis}\n作風：{context.artwork["style"]}',
        f'確認したい観点：{request["question"] or "上記の観点で全体を確認"}',
        '対象の脚本・期待する演出：\n' + json.dumps(pages, ensure_ascii=False, indent=2),
        'キャラクター設定（添付のない基準画像を見たかのように扱わない）：\n' + json.dumps(characters, ensure_ascii=False, indent=2),
        '添付する参照画像の対応（ファイル名は画像ID.png）：' + json.dumps([dict(character=c['name'], image_id=i['id'], view=i['view'], note=i['note']) for c in context.artwork['characters'] for i in c['references'] if i['id'] in request['reference_ids']], ensure_ascii=False),
        '以下の形式でJSONだけを返してください。request_id と story_version_id は変更しないでください。\n' + json.dumps(response_example(request), ensure_ascii=False, indent=2),
    ])


def parse_ai_review(raw, requests, context):
    data = load_json_payload(raw)
    try:
        if not isinstance(data, dict) or type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise ValueError('schema_version は1を指定してください。')
        validate_id(data['request_id']); validate_id(data['story_version_id'])
        request = next((r for r in requests if r['id'] == data['request_id']), None)
        if request is None: raise ValueError('依頼IDが見つかりません。このプロジェクトのレビュー資料を使ってください。')
        if request['story_version_id'] != data['story_version_id'] or data['story_version_id'] != context.latest['id'] or request['snapshot']['token'] != context.snapshot['token']:
            raise ValueError('古いストーリー・画像・編集データに対するレビューです。新しい依頼を作成してください。過去の履歴は保持しています。')
        if not isinstance(data['issues'], list) or len(data['issues']) > 200: raise ValueError('issues は200件以内の配列にしてください。')
        issues, seen = [], set()
        for index, item in enumerate(data['issues'], 1):
            target(item, context.pages)
            if not any(t['page'] == item['page'] and (t['panel'] is None or t['panel'] == item['panel']) for t in request['targets']):
                raise ValueError(f'指摘{index}はレビュー依頼の対象外です。')
            for key in ('category', 'message', 'reason', 'suggestion'): text(item[key], f'指摘{index}の{key}')
            if item['severity'] not in SEVERITIES or item['target'] not in CORRECTION_TARGETS or type(item['needs_human_check']) is not bool:
                raise ValueError(f'指摘{index}の重要度・修正対象・人間確認の形式が不正です。')
            key = digest(item)
            if key in seen: raise ValueError(f'指摘{index}が重複しています。')
            seen.add(key)
            issue = make_issue(context.project_id, **{k: item[k] for k in ('category', 'message', 'page', 'panel', 'severity', 'target', 'reason', 'suggestion')})
            issue['needs_human_check'] = item['needs_human_check']
            issues.append(issue)
        if len({i['id'] for i in issues}) != len(issues): raise ValueError('同じ対象・内容の指摘が重複しています。')
        return dict(id=str(uuid4()), created_at=now_iso(), request_id=request['id'], issues=issues, reviewed=False,
                    decisions={i['id']: '未確認' for i in issues})
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('レビューJSONに必要な項目がありません。依頼の回答用JSON形式に合わせてください。') from error
