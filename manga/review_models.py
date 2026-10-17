"""品質レビュー専用の形式・状態。未確認を合格として扱わない。"""
from datetime import datetime
from manga.models import validate_id

MANUAL_ITEMS = {
    'character': '顔・髪型・服装の統一', 'continuity': '背景・小物の連続性',
    'emotion': 'ストーリーに合う表情', 'anatomy': '人体・手指の自然さ',
    'reading': 'コマ割り・視線誘導', 'occlusion': '吹き出しが顔や重要部分を隠していない',
    'dialogue': 'セリフの配置・読み順',
}
MANUAL_STATES = ('未確認', '問題なし', '要修正', '対象外')
TASK_STATES = ('未対応', '対応中', '修正済み', '再確認待ち', '確認完了', '対応不要')
SEVERITIES = ('高', '中', '低')
CORRECTION_TARGETS = {'image': '画像', 'direction': '作画指示', 'bubble': '吹き出し', 'layout': 'レイアウト', 'story': 'ストーリー', 'settings': '出力設定'}


def text(value, label, maximum=4000, *, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{label}は{maximum}文字以内の文字列で指定してください。')
    return value


def integer(value, label, low=1, high=100):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}は{low}〜{high}の整数にしてください。')
    return value


def timestamp(value):
    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError('日時の形式が不正です。')


def target(value, pages=None, *, global_ok=False):
    p, c = value['page'], value['panel']
    if p is None and global_ok:
        if c is not None: raise ValueError('対象ページが必要です。')
        return
    integer(p, 'ページ番号')
    if c is not None: integer(c, 'コマ番号', 1, 6)
    if pages is not None:
        page = next((item for item in pages if item['page'] == p), None)
        if page is None or (c is not None and c not in [x['panel'] for x in page['panels']]):
            raise ValueError(f'{p}ページ・{c or "全体"}コマは現在のストーリーに存在しません。')


def snapshot_valid(value):
    text(value['token'], '検査データ識別子', 64)
    if value['story_version_id'] is not None: validate_id(value['story_version_id'])
    if not isinstance(value['pages'], dict): raise ValueError('対象ページ一覧が不正です。')
    for key, page in value['pages'].items():
        if str(integer(int(key), 'ページ番号')) != key: raise ValueError('ページ番号が不正です。')
        text(page['fingerprint'], 'ページ識別子', 64)
        validate_id(page['story_version_id'])
        for field in ('artwork_revision', 'composition_revision'):
            if page[field] is not None: integer(page[field], '版番号', 0, 10**12)
        for image_id in page['image_ids']: validate_id(image_id)
        text(page['direction_digest'], '作画識別子', 64)
        text(page['composition_digest'], '編集識別子', 64)


def issue_valid(issue):
    validate_id(issue['id']); target(issue, global_ok=True)
    for key in ('category', 'message', 'reason', 'suggestion'):
        text(issue[key], key)
    if issue['severity'] not in SEVERITIES or issue['target'] not in CORRECTION_TARGETS or type(issue['blocks_export']) is not bool:
        raise ValueError('問題の重要度・修正対象が不正です。')


def empty_review(project_id):
    return dict(schema_version=1, project_id=project_id, revision=0, scans=[], manual={}, previews={}, finals={}, tasks=[], ai_requests=[], ai_reviews=[], events=[])


def validate_review(data, project_id):
    if type(data['schema_version']) is not int or data['schema_version'] != 1 or data['project_id'] != project_id:
        raise ValueError('品質レビューの形式が不正です。')
    integer(data['revision'], 'レビュー版', 0, 10**12)
    seen = set()
    for name in ('scans', 'tasks', 'ai_requests', 'ai_reviews', 'events'):
        if not isinstance(data[name], list): raise ValueError('履歴一覧が不正です。')
        for row in data[name]:
            validate_id(row['id'])
            if row['id'] in seen: raise ValueError('履歴IDが重複しています。')
            seen.add(row['id']); timestamp(row['created_at'])
    for row in data['scans']:
        snapshot_valid(row['snapshot'])
        if type(row['completed']) is not bool or not isinstance(row['checks'], list): raise ValueError('検査結果が不正です。')
        for p in row['pages'] + row['rendered_pages']: integer(p, '検査ページ')
        for issue in row['issues']: issue_valid(issue)
        if len({i['id'] for i in row['issues']}) != len(row['issues']): raise ValueError('問題IDが重複しています。')
        if row['sns_width'] not in (None, 600, 1080): raise ValueError('SNS幅が不正です。')
    for name in ('manual', 'previews', 'finals'):
        if not isinstance(data[name], dict): raise ValueError('ページ確認一覧が不正です。')
        for key, row in data[name].items():
            integer(int(key), '確認ページ'); snapshot_valid(row['snapshot']); timestamp(row['updated_at'])
            if name == 'manual':
                if set(row['answers']) != set(MANUAL_ITEMS) or any(x not in MANUAL_STATES for x in row['answers'].values()): raise ValueError('手動確認項目が不正です。')
                text(row['notes'], '確認メモ', required=False)
    for row in data['tasks']:
        issue_valid(row['issue']); snapshot_valid(row['snapshot']); timestamp(row['updated_at'])
        if row['status'] not in TASK_STATES or row['origin'] not in ('auto', 'manual', 'ai'): raise ValueError('タスク状態が不正です。')
        text(row['method'], '修正方法', required=False)
        if row['verified_snapshot'] is not None: snapshot_valid(row['verified_snapshot'])
    for row in data['ai_requests']:
        snapshot_valid(row['snapshot']); validate_id(row['story_version_id'])
        if not row['targets']: raise ValueError('レビュー対象がありません。')
        for item in row['targets']: target(item)
        for image in row['reference_ids']: validate_id(image)
        text(row['question'], '確認観点', required=False)
        if type(row['include_pages']) is not bool: raise ValueError('資料のページ添付設定が不正です。')
        if 'prompt' in row: text(row['prompt'], '依頼プロンプト', 1_000_000)
    request_ids = {r['id'] for r in data['ai_requests']}
    for row in data['ai_reviews']:
        if row['request_id'] not in request_ids: raise ValueError('元のレビュー依頼がありません。')
        if type(row['reviewed']) is not bool: raise ValueError('AI提案の確認状態が不正です。')
        for issue in row['issues']:
            issue_valid(issue)
            if type(issue['needs_human_check']) is not bool: raise ValueError('人間確認の指定が不正です。')
        if set(row['decisions']) != {i['id'] for i in row['issues']} or any(x not in ('未確認', 'タスク化', '対応不要') for x in row['decisions'].values()): raise ValueError('AI提案の採否が不正です。')
    return data
