"""漫画MVPの依存順。生成・採用・描画の実処理を複製しない。"""
from manga.storage import StorageError, ProjectStore
from manga.voice_models import digest


def action(kind, key=None, **args):
    from manga.manga_mvp import LABELS
    return dict(kind=kind, key=key or kind, label=LABELS[kind], args=args)


def check_story(engine):
    history = engine.story.stories.load_history()
    if history and (len(history) != 1 or not any(a['story_version_id'] == history[-1]['id'] for a in engine.story.store.load()['adoptions'])):
        raise StorageError('制作脚本が別の版へ変更されました。既存版を保持し、一括処理を停止します。')
    return history[-1] if history else None


def proposal(engine):
    data = engine.characters.proposals.load()
    return data['proposals'][-1] if data['proposals'] else None


def image_candidates(engine, kind):
    data = engine.images.store.load()
    jobs = {j['id']: j for j in data['jobs']}
    return [c for c in data['candidates'] if jobs[c['job_id']]['plan']['kind'] == kind]


def selected(engine, kind, target):
    import hashlib
    from manga.artwork_models import character_fields
    art = engine.story.artwork.load()
    entry = next((c for c in art['characters'] if c['id'] == target), None) if kind == 'character' else art['panels'].get(target)
    identifier = entry.get('primary_reference_id' if kind == 'character' else 'selected_image_id') if entry else None
    if identifier:
        image = next(i for i in entry['references' if kind == 'character' else 'candidates'] if i['id'] == identifier)
        content = engine.story.artwork.image_bytes(image)
        data = engine.images.store.load()
        adoption = next((a for a in reversed(data['adoptions']) if a['image_id'] == identifier), None)
        if not adoption or hashlib.sha256(content).hexdigest() != adoption['registered_sha256']:
            raise StorageError('画像の正式採用記録・SHAを照合できません。既存の画像確認画面で確認してください。')
        job = next(j for j in data['jobs'] if j['candidate_id'] == adoption['candidate_id'])
        plan = job['plan']
        if plan['story_version_id'] != engine.story.stories.load_history()[-1]['id'] or plan['source']['style'] != art['style']:
            raise StorageError('画像の脚本版・画風が変わりました。確認前に再利用しません。')
        if kind == 'character' and plan['source']['characters'][0]['profile'] != character_fields(entry):
            raise StorageError('人物設定が基準画像の生成時から変更されています。画像画面で再確認してください。')
        if kind == 'panel':
            from manga.artwork_models import image_context_changed
            if image_context_changed(image, entry, art):
                raise StorageError('採用画像の元設定が変わりました。既存の画像確認画面で再確認してください。')
    return identifier


def pending_candidates(engine, kind):
    data = engine.images.store.load()
    jobs = {j['id']:j for j in data['jobs']}
    result = []
    for c in image_candidates(engine, kind):
        plan = jobs[c['job_id']]['plan']
        target = plan['character_id'] if kind == 'character' else f"{plan['page']}:{plan['panel']}"
        if not selected(engine, kind, target):
            engine.images.candidate_bytes(c)
            from manga.image_ai_planning import current
            current(engine.directory, engine.project_id, plan, hashed=True)
            result.append(c)
    return result


def waiting_images(engine):
    data = engine.images.store.load()
    for kind in ('character', 'panel'):
        pending = pending_candidates(engine, kind)
        unreviewed = [c for c in pending if not any(r['candidate_id'] == c['id'] and r['sha256'] == c['sha256'] for r in data['reviews'])]
        if unreviewed:
            return unreviewed
    return []


def next_action(engine):
    import json
    from manga.character_ai_service import latest_bibles, bible_stale
    path = engine.story.store.safe('story_ai/adoption_pending.json')
    if path.exists() and json.loads(path.read_text())['state'] == 'pending':
        return action('recover_story'), ''
    if engine.characters.proposals.pending():
        return action('recover_person'), ''
    if engine.images.store.pending():
        return action('recover_image'), ''
    data = engine.story.store.load()
    latest = check_story(engine)
    if not data['concept_batches']:
        return action('concepts'), ''
    if not data['selections']:
        return action('select'), ''
    if not data['candidates']:
        return action('story'), ''
    if not latest or not any(f['story_version_id'] == latest['id'] for f in engine.story.editorial.load()['finalizations']):
        return action('adopt_story'), ''
    prop = proposal(engine)
    if not prop:
        return action('characters'), ''
    request = engine.characters.proposals.request(engine.characters.proposals.load(), prop['request_id'])
    extraction = request['character_ai']['extraction']
    if extraction['unknown_speakers']:
        return None, '話者が不明なセリフがあります。脚本・人物画面で確認してください。'
    for item in prop['payload']['items']:
        if prop['decisions'][item['id']] == '確認待ち':
            candidate = next(c for c in extraction['candidates'] if c['id'] == item['id'])
            if candidate['matches']:
                return None, '既存人物との同定が必要です。人物画面で確認してください。'
            return action('person', 'person/' + item['id'], item_id=item['id']), ''
    snapshot = engine.characters.snapshot(hashed=True)
    people = [c for c in snapshot['artwork']['characters'] if not c['deleted_at']]
    if not people:
        return None, '制作する人物が未登録です。人物画面で確認してください。'
    bibles = latest_bibles(snapshot['data'])
    for person in people:
        bible = bibles.get(person['id'])
        if not bible or bible_stale(bible, latest, person, snapshot['references'][person['id']]['images']):
            return action('bible', 'bible/' + person['id'], character_id=person['id']), ''
    for kind, targets in [('character', [p['id'] for p in people])]:
        candidates = image_candidates(engine, kind)
        jobs = {j['id']:j for j in engine.images.store.load()['jobs']}
        for identifier in targets:
            if selected(engine, kind, identifier):
                continue
            found = next((c for c in candidates if jobs[c['job_id']]['plan']['character_id'] == identifier), None)
            if not found:
                from manga.manga_mvp_images import enabled, next_image
                if enabled(engine):
                    return next_image(engine, 'character', character_id=identifier)
                if not mock_images(engine):
                    return None, '画像APIはモデル・最大料金・有料送信の承認待ちです。実装上無効のため送信しません。手動画像制作も利用できます。'
                return action('reference', 'reference/' + identifier, character_id=identifier), ''
    pending = pending_candidates(engine, 'character')
    if pending:
        return _image_review_action(engine, pending)
    from manga.direction_drafts import prepare_drafts
    batch = prepare_drafts(engine.story.artwork)
    for row in batch['items']:
        if not row['existing']:
            if row['issues']:
                return None, row['key'] + '：' + ' / '.join(row['issues'])
            return action('direction', 'direction/' + row['key'], target=row['key']), ''
    for page in latest['story']['pages']:
        config = engine.pages.load()['pages'].get(str(page['page']))
        if config is None:
            return action('layout', 'layout/' + str(page['page']), page=page['page']), ''
        if config['source_page'] != page or config['source_version_id'] != latest['id']:
            return None, 'ページ構成が脚本と一致しません。漫画編集画面で確認してください。'
    candidates = image_candidates(engine, 'panel')
    jobs = {j['id']:j for j in engine.images.store.load()['jobs']}
    for page in latest['story']['pages']:
        for panel in page['panels']:
            p, c = page['page'], panel['panel']
            if selected(engine, 'panel', f'{p}:{c}'):
                continue
            if not any(jobs[i['job_id']]['plan']['page'] == p and jobs[i['job_id']]['plan']['panel'] == c for i in candidates):
                from manga.manga_mvp_images import enabled, next_image
                if enabled(engine):
                    return next_image(engine, 'panel', page=p, panel=c)
                if not mock_images(engine):
                    return None, '画像APIの有料送信は無効です。確認済みの手動画像を取り込むか、API選定の承認後に再開してください。'
                return action('panel', f'panel/{p}:{c}', page=p, panel=c), ''
    pending = pending_candidates(engine, 'panel')
    if pending:
        return _image_review_action(engine, pending)
    for item in engine.automation.plan()['actions']:
        if item['kind'] in ('page', 'pdf', 'png_zip'):
            return action('export', 'export/' + item['key'], action=item), ''
    exports = engine.studio.load()['exports']
    if not all(any(e['kind'] == kind and e.get('artifact_role') == 'manga' for e in exports) for kind in ('page', 'pdf', 'png_zip')):
        return None, 'PNG・PDF出力の条件が不足しています。既存ダッシュボードで確認してください。'
    for event in exports:
        if event.get('artifact_role') == 'manga':
            engine.studio.export_bytes(event)
    return None, 'outputs_ready'


def _image_review_action(engine, pending):
    data = engine.images.store.load()
    if any(not any(r['candidate_id'] == c['id'] and r['sha256'] == c['sha256'] for r in data['reviews']) for c in pending):
        return None, f'画像候補{len(pending)}件をまとめて目視・権利確認してください。正式採用は別操作です。'
    return action('adopt_image', 'adopt/' + pending[0]['id'], candidate_id=pending[0]['id']), ''


def story_job(engine, kind):
    service = engine.story
    data = service.store.load()
    job = next((j for j in data['jobs'] if j['kind'] == kind), None)
    if not job:
        mode = engine.load()['plan']['mode']
        provider = 'mock' if mode == 'mock' else 'gemini-text'
        if provider != 'mock':
            engine.api_consent()
        estimate = service.estimate(kind, provider=provider, mode=mode)
        job = service.create_job(kind, expected_revision=data['revision'], provider=provider, mode=mode,
                                 approval=estimate['approval_key'] if provider != 'mock' else None)
    if job['state'] == 'queued':
        if job['provider'] != 'mock':
            engine.api_consent()
        service.run_job(job['id'], project_cap_jpy=engine.load()['plan'].get('max_budget_jpy', '500'))
    elif job['state'] in ('running', 'paused'):
        service.recover(job['id'])  # 保存済み応答の復旧だけ。HTTP再送なし。
    current = service.store.load()
    if not (current['concept_batches'] if kind == 'concepts' else current['candidates']):
        raise ValueError('APIの応答または請求が未確認です。既存の履歴・復旧画面で照合してください。')


def execute(engine, item):
    kind, args = item['kind'], item['args']
    service = engine.story
    if kind == 'recover_story':
        service.recover_adoption()
        return
    if kind == 'recover_person':
        engine.characters.proposals.resume()
        return
    if kind == 'recover_image':
        engine.images.recover_adoption(confirmed=True)
        return
    latest = check_story(engine)
    if kind in ('concepts', 'story'):
        story_job(engine, kind)
    elif kind == 'select':
        data = service.store.load()
        service.select_concept(data['concept_batches'][0]['ideas'][0]['id'], expected_revision=data['revision'])
    elif kind == 'adopt_story':
        if not latest:
            data = service.store.load()
            service.adopt(data['candidates'][0]['id'], expected_revision=data['revision'], expected_story_id=None, confirmed=True)
            latest = service.stories.load_history()[-1]
        service.editorial.finalize(expected_version_id=latest['id'], expected_revision=service.editorial.load()['revision'])
    elif kind == 'characters':
        chars = engine.characters
        data = chars.proposals.load()
        if data['requests']:
            old = data['requests'][-1]
            if old.get('character_ai', {}).get('provider') == 'gemini-text':
                result = chars.recover_gemini(old['id'])
                if not result['proposal']:
                    raise ValueError('人物応答が不正または請求未確認です。再送しません。')
        elif engine.load()['plan']['mode'] == 'mock':
            chars.proposals.create_mock(ProjectStore(engine.directory).load(engine.project_id), latest['id'], expected_revision=data['revision'], additional_names=_additional_names(latest))
        else:
            engine.api_consent()
            project = ProjectStore(engine.directory).load(engine.project_id)
            plan = chars.preview_gemini(project, latest['id'], mode=engine.load()['plan']['mode'])
            result = chars.generate_gemini(project, latest['id'], mode=engine.load()['plan']['mode'], approval=plan['approval_key'], expected_revision=plan['revision'], project_cap_jpy=engine.load()['plan'].get('max_budget_jpy', '500'))
            if not result['proposal']:
                raise ValueError('人物応答または請求が未確認です。自動再送しません。')
        if not proposal(engine):
            raise ValueError('人物を一意に抽出できません。既存の人物画面で確認してください。')
    elif kind == 'person':
        prop = proposal(engine)
        row = next(r for r in prop['payload']['items'] if r['id'] == args['item_id'])
        engine.characters.proposals.adopt_fields(prop['id'], row['id'], row['fields'], list(row['fields']), None,
            expected_revision=engine.characters.proposals.load()['revision'], expected_artwork_revision=service.artwork.load()['revision'],
            expected_editorial_revision=service.editorial.load()['revision'], expected_character_revision=engine.characters.store.load()['revision'], identity_confirmed=True)
    elif kind == 'bible':
        engine.characters.save_bible(args['character_id'], version_id=latest['id'], expected_revision=engine.characters.store.load()['revision'],
            expected_artwork_revision=service.artwork.load()['revision'], locked_fields=[], aliases=[], exceptions=[],
            reason='新規作品のMVP一括制作承認に基づく設定保存。外見・権利・公開品質は未確認。', confirmed=True)
    elif kind in ('reference', 'panel'):
        from manga.manga_mvp_images import enabled, execute as execute_image
        if enabled(engine):
            execute_image(engine, 'character' if kind == 'reference' else 'panel', **args)
            return
        if not mock_images(engine):
            raise ValueError('画像APIは無効です。')
        plan = engine.images.plan('character' if kind == 'reference' else 'panel', **args)
        job = engine.images.create_job(plan, provider='mock', expected_revision=engine.images.store.load()['revision'])
        if job['state'] == 'queued':
            engine.images.run(job['id'])
        elif job['state'] != 'ready':
            engine.images.recover(job['id'], confirmed=True)
    elif kind == 'adopt_image':
        engine.images.adopt(args['candidate_id'], expected_revision=engine.images.store.load()['revision'],
                            expected_artwork_revision=service.artwork.load()['revision'], confirmed=True)
    elif kind == 'direction':
        from manga.direction_drafts import prepare_drafts, save_drafts
        batch = prepare_drafts(service.artwork)
        result = save_drafts(service.artwork, batch, [args['target']], confirmed=True)
        if result['errors']:
            raise StorageError('作画指示の保存が停止しました。保存済みコマを保持して再開してください。')
    elif kind == 'layout':
        from manga.page_layout import default_page
        data = engine.pages.load()
        page = latest['story']['pages'][args['page'] - 1]
        engine.pages.save_page(args['page'], default_page(engine.project_id, page, latest['id'], data['settings']), story_version_id=latest['id'], expected_revision=data['revision'])
    elif kind == 'export':
        engine.automation.execute(args['action'])
    else:
        raise ValueError('許可していない制作処理です。')


def _additional_names(latest):
    # 無料モックの既存ひな形が明記する2名。実APIの曖昧な人物には使わない。
    return [name for name in ('主人公', '友人') if any(name in panel['description'] for page in latest['story']['pages'] for panel in page['panels'])]


def mock_images(engine):
    plan = engine.load()['plan']
    return plan.get('image_mode', 'mock' if plan['mode'] == 'mock' else 'manual') == 'mock'
