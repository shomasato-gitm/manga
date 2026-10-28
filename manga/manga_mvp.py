"""新規作品専用の漫画制作進行。素材の正本と課金実行は既存Serviceに委譲する。"""
import copy
import threading
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from manga.automation import Automation
from manga.models import Project, now_iso, validate_id
from manga.storage import ProjectStore, StorageError
from manga.studio_storage import StudioStore
from manga.story_ai_service import StoryAIService
from manga.character_ai_service import CharacterAIService
from manga.image_ai_service import ImageAIService
from manga.page_storage import PageStore
from manga.voice_models import digest

MODES = ('mock', 'verified-free', 'paid')
LABELS = {
    'concepts': '企画候補を生成', 'select': '先頭の企画を制作に使用',
    'story': '脚本候補を生成', 'adopt_story': '新規作品の制作脚本を保存・確定',
    'characters': '人物設定案を生成', 'person': '新規人物を制作登録',
    'bible': '人物設定をバイブルへ保存', 'reference': '基準画像候補を準備',
    'adopt_image': '確認済み画像を正式採用', 'direction': '脚本から作画指示を保存',
    'layout': 'コマとセリフのページ配置を保存', 'panel': 'コマ画像候補を準備',
    'export': '漫画PNG・PDF・PNG ZIPを出力',
    'recover_story': '脚本の採用保存を再開（再送なし）',
    'recover_person': '人物の採用保存を再開', 'recover_image': '画像の採用保存を再開',
}


def creation_plan(directory, values, nonce, *, mode='mock', card=None):
    """通信・予約なし。利用者が承認する新規作品、操作範囲、最大3送信を固定する。"""
    from manga.artwork_models import text
    from manga.story_ai_models import settings
    validate_id(nonce)
    if mode not in MODES:
        raise ValueError('制作方式を選択してください。')
    config = settings(dict(values, mood='テーマに沿った読みやすい短編', max_cycles=0, concept_count=3))
    style = text(values.get('style', ''), '画風', 2000, True)
    identifier = str(uuid5(UUID(nonce), digest(dict(config=config, style=style, mode=mode))))
    maximum = Decimal('0')
    if mode != 'mock':
        from manga.story_ai_gemini import check_mode
        from manga.story_ai_pricing import evidence
        from manga.gemini_models import DEFAULT_MODEL
        if not card or card.get('provider') != 'gemini-text' or card.get('model') != DEFAULT_MODEL:
            raise ValueError('既存のAI企画・ストーリー画面でGemini 3.5の共通料金JSONを確認・保存してください。')
        check_mode(card, mode)
        maximum = Decimal(evidence(card)['computed_max_jpy'])
        if not StoryAIService.api_enabled(mode):
            raise ValueError('テキストAPIはOFFです。既存の利用条件と機能フラグを確認してください。')
        if maximum * 3 > 500:
            raise ValueError('テキスト3送信の最大額が作品500円を超えます。無料モックまたは手動制作を選択してください。')
    result = dict(project_id=identifier, settings=config, style=style, mode=mode,
                  card=copy.deepcopy(card) if mode != 'mock' else None,
                  text_calls=0 if mode == 'mock' else 3, per_call_max_jpy=str(maximum),
                  text_max_jpy=str(maximum * 3), image_api_enabled=False,
                  destination=str(Path(directory) / identifier))
    if mode != 'mock':
        from manga.budget.config import open_budget
        summary = open_budget(directory).summary(identifier)
        result['budget'] = {key:str(summary[key]) for key in ('project_remaining_jpy', 'monthly_remaining_jpy', 'project_reserved_jpy')}
        result['budget']['blocked'] = bool(summary['blocked'])
    result['token'] = digest(result)
    return result


def create(directory, plan, *, approved=False):
    """同じnonceの二重クリックでは同じ新規作品を返す。既存作品は引き継がない。"""
    import fcntl
    from manga.budget.config import open_budget, budget_path
    from manga.budget import BudgetedExecutor
    if approved is not True or digest({k:v for k,v in plan.items() if k != 'token'}) != plan['token']:
        raise ValueError('対象・保存先・制作採用の範囲・最大費用・外部送信への明示承認が必要です。')
    directory = Path(directory)
    identifier = validate_id(plan['project_id'])
    if plan['mode'] not in MODES or plan['text_calls'] != (0 if plan['mode'] == 'mock' else 3) or Decimal(plan['text_max_jpy']) != Decimal(plan['per_call_max_jpy']) * 3 or not Decimal('0') <= Decimal(plan['text_max_jpy']) <= Decimal('500'):
        raise ValueError('制作方式・送信回数・最大額が不正です。')
    if plan['destination'] != str(directory / identifier) or plan['image_api_enabled'] is not False:
        raise ValueError('保存先または画像APIの停止設定が一致しません。')
    if plan['mode'] != 'mock':
        from manga.story_ai_gemini import check_mode
        from manga.story_ai_pricing import evidence
        from manga.gemini_models import DEFAULT_MODEL
        if plan['card'].get('provider') != 'gemini-text' or plan['card'].get('model') != DEFAULT_MODEL:
            raise ValueError('承認対象のGemini 3.5モデル設定を確認してください。')
        check_mode(plan['card'], plan['mode'])
        if Decimal(evidence(plan['card'])['computed_max_jpy']) != Decimal(plan['per_call_max_jpy']) or not StoryAIService.api_enabled(plan['mode']):
            raise ValueError('料金・利用条件が変わりました。見積を再確認してください。')
        if not BudgetedExecutor(open_budget(directory)).dry_run(project_id=identifier, quote_jpy=plan['text_max_jpy'], provider='gemini-text')['possible']:
            raise ValueError('共通残予算不足または請求確認待ちです。無料モック・手動制作に切り替えるか、既存予算画面で照合してください。')
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / '.manga-mvp-create.lock'
    if lock_path.is_symlink():
        raise StorageError('制作ロックのリンクを拒否しました。')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        projects = ProjectStore(directory)
        studio = StudioStore(directory, identifier)
        run = studio.load().get('manga_mvp')
        if run:
            if run.get('approval_token') != plan['token']:
                raise StorageError('この作品は別の制作条件で開始済みです。上書きしません。')
        else:
            if projects._path(identifier).exists() or studio.path.exists() or any((directory / identifier).glob('*.json')):
                raise StorageError('既存作品には一括制作を開始しません。新規作品として作成してください。')
            run = dict(schema_version=1, project_id=identifier, approval_token=plan['token'], plan=copy.deepcopy(plan),
                       approved_at=now_iso(), state='ready', stop_requested=False, events=[], guard=None,
                       ledger_path=str(budget_path(directory).resolve()) if plan['mode'] != 'mock' else None)
            studio.update(lambda data: data.update(manga_mvp=run))
        if not projects._path(identifier).exists():
            config = plan['settings']
            projects.save(Project(id=identifier, title=config['theme'][:100], genre=config['genre'], synopsis=config['theme'], page_count=config['page_count']))
        engine = MangaMVP(directory, identifier)
        engine.initialize()
        return projects.load(identifier)


class MangaMVP:
    def __init__(self, directory, project_id):
        self.directory = Path(directory)
        self.project_id = validate_id(project_id)
        self.studio = StudioStore(directory, project_id)
        self.story = StoryAIService(directory, project_id)
        self.characters = CharacterAIService(directory, project_id)
        self.images = ImageAIService(directory, project_id)
        self.pages = PageStore(directory, project_id)
        self.automation = Automation(directory, project_id)

    def load(self):
        run = self.studio.load().get('manga_mvp')
        if not isinstance(run, dict) or run.get('schema_version') != 1 or run.get('project_id') != self.project_id:
            raise StorageError('この作品の一括制作記録がありません。既存作品は上書きしません。')
        plan = run.get('plan', {})
        if plan.get('project_id') != self.project_id or plan.get('mode') not in MODES or plan.get('image_api_enabled') is not False or run.get('approval_token') != plan.get('token') or digest({k:v for k,v in plan.items() if k != 'token'}) != plan.get('token'):
            raise StorageError('制作条件・承認の記録が一致しません。初期化せず確認してください。')
        per = Decimal(plan['per_call_max_jpy'])
        if not per.is_finite() or plan['text_calls'] != (0 if plan['mode'] == 'mock' else 3) or Decimal(plan['text_max_jpy']) != per * 3 or not 0 <= per * 3 <= 500 or (plan['mode'] != 'mock' and per <= 0):
            raise StorageError('承認済みの送信回数・最大額が不正です。')
        if plan['mode'] != 'mock':
            from manga.gemini_models import DEFAULT_MODEL
            if plan['card'].get('provider') != 'gemini-text' or plan['card'].get('model') != DEFAULT_MODEL:
                raise StorageError('承認したGeminiモデルが一致しません。')
        return run

    def update(self, **changes):
        self.studio.update(lambda data: data['manga_mvp'].update(copy.deepcopy(changes)))

    def source(self):
        from manga.studio_assets import asset_token
        from manga.ui_data import signature
        artwork, pages = self.story.artwork.load(), self.pages.load()
        references = [(r['id'], signature(self.story.artwork.image_path(r))) for c in artwork['characters'] for r in c['references']]
        return digest(dict(project=ProjectStore(self.directory).load(self.project_id).to_dict(),
            story=self.story.stories.load_history(), artwork=artwork, pages=pages,
            bibles=self.characters.store.load(), editorial=self.story.editorial.load(),
            images=self.images.store.load(), settings=self.story.store.load()['settings'],
            references=references, assets=asset_token(self.directory / self.project_id, artwork, pages['settings'])))

    def initialize(self):
        run = self.load()
        with self.automation.lock():
            if self.story.store.load()['settings'] is None:
                self.story.configure(run['plan']['settings'], run['plan']['card'], expected_revision=self.story.store.load()['revision'])
            if self.story.artwork.load()['style'] != run['plan']['style']:
                if self.story.artwork.load()['revision']:
                    raise StorageError('保存済みの画風は上書きしません。')
                self.story.artwork.save_style(run['plan']['style'], expected_revision=0)
            if self.load()['guard'] is None:
                self.update(guard=self.source())

    def plan(self):
        from manga.manga_mvp_steps import next_action
        run = self.load()
        source = self.source()
        changed = source != run['guard']
        action, reason = (None, '保存済み素材が変更されました。制作を止めて再検査してください。') if changed else next_action(self)
        status = '要再確認' if changed else '実行可能' if action else '画像の確認待ち' if reason.startswith('画像候補') else '出力済み・公開前確認待ち' if reason == 'outputs_ready' else '停止中'
        if run['state'] == 'error' and not changed:
            status = 'エラー・復旧待ち'
        return dict(action=action, reason=reason, status=status, changed=changed,
                    token=digest(dict(run=run, source=source, action=action, reason=reason)),
                    destination=str(self.directory / self.project_id))

    def api_consent(self):
        from manga.budget.config import open_budget, budget_path
        from manga.budget import BudgetedExecutor
        from manga.story_ai_gemini import check_mode
        run = self.load()
        plan = run['plan']
        card = self.story.store.load()['card']
        if self.story.store.load()['settings'] != plan['settings']:
            raise StorageError('承認後に制作条件が変わりました。以前の範囲でAPIを送信しません。')
        if plan['mode'] == 'mock':
            raise ValueError('無料モックには外部送信権がありません。')
        if digest(card) != digest(plan['card']) or str(budget_path(self.directory).resolve()) != run['ledger_path'] or not Path(run['ledger_path']).is_file():
            raise StorageError('承認済みの料金設定または共有台帳が変わりました。別の台帳で送信しません。')
        check_mode(card, plan['mode'])
        story_data = self.story.store.load()
        jobs = {j['id']:j for j in story_data['jobs']}
        requests = [r for r in story_data['requests'] if jobs[r['job_id']]['provider'] != 'mock']
        requests += [r for r in self.characters.proposals.load()['requests'] if r.get('character_ai', {}).get('provider') == 'gemini-text']
        if len(requests) >= plan['text_calls']:
            raise ValueError('承認済みの最大3送信を使用済みです。自動再送・評価・改善の追加送信はしません。')
        ledger = open_budget(self.directory)
        if not BudgetedExecutor(ledger).dry_run(project_id=self.project_id, quote_jpy=plan['per_call_max_jpy'], provider='gemini-text')['possible']:
            raise ValueError('予算不足または請求不明です。共通予算管理で照合するまで停止します。')

    def run(self, token, *, approved=False, limit=128):
        from manga.manga_mvp_steps import execute
        if approved is not True or type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError('今回の処理対象と再開を確認してください。')
        with self.automation.lock():
            plan = self.plan()
            if token != plan['token'] or plan['changed']:
                raise StorageError('素材または制作状態が変わりました。再検査してください。')
            self.update(state='running', stop_requested=False)
            for _ in range(limit):
                if self.load()['stop_requested']:
                    break
                if self.source() != self.load()['guard']:
                    raise StorageError('制作中に素材が変更されました。上書きせず停止しました。')
                current = self.plan()
                if not current['action']:
                    break
                action = current['action']
                event = dict(key=action['key'], kind=action['kind'], state='running', started_at=now_iso())
                self.update(events=self.load()['events'] + [event])
                try:
                    execute(self, action)
                except Exception:
                    event.update(state='error', finished_at=now_iso(), error='保存・素材・請求を確認してください。自動再送しません。')
                    self.update(state='error', guard=self.source(), events=self.load()['events'][:-1] + [event])
                    raise ValueError('制作を停止しました。保存済みの成果物と請求を確認し、同じ作品から再開してください。') from None
                event.update(state='done', finished_at=now_iso())
                self.update(guard=self.source(), events=self.load()['events'][:-1] + [event])
            self.update(state='stopped')
            return self.plan()

    def start(self, token):
        def work():
            try:
                self.run(token, approved=True)
            except (ValueError, OSError, StorageError):
                pass
        threading.Thread(target=work, daemon=True, name='manga-mvp-' + self.project_id[:8]).start()

    def stop(self):
        self.update(stop_requested=True)

    def reinspect(self, token, *, confirmed=False):
        from manga.manga_mvp_steps import check_story
        with self.automation.lock():
            if confirmed is not True or self.plan()['token'] != token:
                raise ValueError('変更後の素材を確認してください。')
            check_story(self)
            self.update(guard=self.source(), state='stopped')

    def review_images(self, token, checks, note):
        """全対象の人間のレビューだけを記録。正式採用と制作再開は別操作。"""
        from manga.manga_mvp_steps import waiting_images
        with self.automation.lock():
            if self.plan()['token'] != token or self.plan()['changed']:
                raise StorageError('確認対象が変わりました。画像を再表示してください。')
            candidates = waiting_images(self)
            if not candidates:
                raise ValueError('画像の確認待ちはありません。')
            for candidate in candidates:
                data = self.images.store.load()
                if not any(r['candidate_id'] == candidate['id'] and r['sha256'] == candidate['sha256'] for r in data['reviews']):
                    self.images.review(candidate['id'], checks, note, expected_revision=data['revision'])
            self.update(guard=self.source())
