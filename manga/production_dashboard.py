"""9工程の読み取り専用ビュー。進捗・素材・履歴の正本は既存Storeだけ。"""
import json
from pathlib import Path

from manga.artwork_storage import ArtworkStore
from manga.story_storage import StoryStore
from manga.editorial_storage import EditorialStore
from manga.page_storage import PageStore
from manga.studio_storage import StudioStore
from manga.studio_assets import production_fingerprint
from manga.studio_workflow import panel_states
from manga.character_ai_service import source_snapshot, latest_bibles, bible_stale
from manga.character_ai_checks import inspect
from manga.image_ai_storage import ImageAIStore
from manga.review_context import load_context
from manga.review_storage import ReviewStore
from manga.review_status import summary
from manga.storage import StorageError
from manga.ui_data import cached_read, signature
from manga.voice_models import digest
from manga.voice_service import VoiceService
from manga.video_service import VideoService
from manga.video_models import FORMATS
from manga.production_media import evidence, media_token

MANUAL_DELIVERY = {
    'identity':'人物の外見・基準画像・全コマの連続性',
    'pages':'ページの構成・順序・セリフと字幕の意味の一致',
    'sync':'3形式すべての音声・映像・字幕の同期と見切れ',
    'rights':'全素材の出典・利用権と公開先の最新ポリシー',
    'test_material':'テスト画像・確認用トーンの混入がないこと',
    'outputs':'PNG・PDF・3形式MP4を開いて最終確認した',
}


def stage(key, label, done, total, menu, section, action, missing=(), *, stale=False, started=False):
    complete = total > 0 and done == total and not missing and not stale
    state = '完了' if complete else '要確認' if stale else '進行中' if done or started else '未着手'
    return dict(key=key, label=label, done=done, total=total, state=state, menu=menu, section=section,
        action=action, missing=list(missing), detail=' / '.join(missing), optional=False)


def recovery_routes(directory, project_id):
    """保存途中を既存の再開画面へ戻す。ダッシュボードでは復旧を実行しない。"""
    root = Path(directory)/project_id
    result = []
    from manga.assistant_storage import AssistantStore
    from manga.sync_storage import SyncStore
    for pending, label, menu, section in (
        (AssistantStore(directory, project_id).pending, '制作提案の採用保存', '絵コンテ・作画指示', 'AI一括生成・採用'),
        (ImageAIStore(directory, project_id).pending, '画像の採用保存', 'イラスト制作', 'AI画像生成・採用'),
        (SyncStore(directory, project_id).pending, 'GitHub作品同期の保存', '完成・書き出し', 'GitHub作品同期'),
    ):
        if pending():
            result.append(dict(label=label, menu=menu, section=section))
    images = ImageAIStore(directory, project_id).load()
    if any(j['state'] in ('queued','running','artifact_pending','uncertain') for j in images['jobs']):
        result.append(dict(label='画像ジョブの実行・保存状態', menu='イラスト制作', section='AI画像生成・採用'))
    path = root/'story_ai/adoption_pending.json'
    if path.exists():
        try:
            if path.is_symlink() or path.stat().st_size > 48*1024*1024:
                raise ValueError
            if json.loads(path.read_text())['state'] == 'pending':
                result.append(dict(label='脚本の採用保存', menu='企画・ストーリー', section='AI企画・ストーリー'))
        except (ValueError, KeyError, OSError) as error:
            raise StorageError('脚本の採用途中記録を確認してください。上書きしていません。') from error
    voice = VoiceService(directory, project_id).store.load()
    if any(j['state'] in ('running','uncertain','artifact_pending') for j in voice['jobs']):
        result.append(dict(label='音声ジョブの実行・請求・保存状態', menu='完成・書き出し', section='音声制作・字幕'))
    video = VideoService(directory, project_id).store.load()
    if any(j['state'] in ('queued','running','interrupted') for j in video['jobs']):
        result.append(dict(label='動画ジョブの実行・復旧状態', menu='完成・書き出し', section='動画制作・MP4出力'))
    return result


def _export_pages(store, studio, fingerprint, pages):
    png, pdf, errors = set(), set(), []
    from manga.video_renderer import sha_file
    for event in studio['exports']:
        if event['fingerprint'] != fingerprint:
            continue
        if not event.get('path'):
            errors.append('旧出力記録には保存ファイルがありません。PNG・PDFを再出力してください。')
            continue
        try:
            path = store.artifact_path(event)
            if not path.is_file() or path.stat().st_size != event['bytes']:
                raise ValueError('保存済みページ出力が欠損・容量不一致です。')
            if cached_read(path, lambda:sha_file(path), variant='production-export-sha') != event['sha256']:
                raise ValueError('保存済みページ出力のSHAが一致しません。')
            if event['kind'] in ('page','png_zip'):
                png.update(event['pages'])
            if event['kind'] == 'pdf':
                pdf.update(event['pages'])
        except (StorageError, OSError, ValueError) as error:
            errors.append(str(error))
    return png & pages, pdf & pages, list(dict.fromkeys(errors))


def dashboard(project, directory):
    directory = Path(directory)
    root = directory/project.id
    history = StoryStore(directory, project.id).load_history()
    latest = history[-1] if history else None
    art = ArtworkStore(directory, project.id).load()
    editorial = EditorialStore(directory, project.id).load()
    composition = PageStore(directory, project.id).load()
    studio_store = StudioStore(directory, project.id)
    studio = studio_store.load()
    image_data = ImageAIStore(directory, project.id).load()
    voice = VoiceService(directory, project.id)
    voice_data = voice.store.load()
    video = VideoService(directory, project.id).store.load()
    recoveries = recovery_routes(directory, project.id)
    panels = panel_states(latest['story'], art, root, latest['id']) if latest else []
    n = len(panels)
    pages = {p['page'] for p in latest['story']['pages']} if latest else set()
    confirmed = bool(latest and any(f['story_version_id'] == latest['id'] and f['source_story'] == latest['story'] for f in editorial['finalizations']))
    rows = []
    adopted = bool(latest and project.title.strip() and project.synopsis.strip() and len(pages) == project.page_count)
    rows.append(stage('story','企画・脚本の正式採用',int(adopted),1,'企画・ストーリー','生成・取り込み',
        '作品の企画を保存し、脚本を正式取り込み・採用する', [] if adopted else ['作品のあらすじ・設定ページ数と正式脚本を確認してください。'],started=bool(history)))
    rows.append(stage('finalized','脚本の最終確定',int(confirmed),1,'企画・ストーリー','比較・脚本確定',
        '最新の正式脚本を比較・確定する',[] if confirmed else ['最新の脚本版に対する明示的な確定が必要です。'],stale=bool(editorial['finalizations'] and not confirmed)))
    people = [c for c in art['characters'] if not c['deleted_at']]
    character_missing = []
    ready_people = set()
    issues = []
    bibles = {}
    if latest:
        from manga.character_ai_storage import CharacterAIStore
        character_store = CharacterAIStore(directory, project.id)
        snapshot = source_snapshot(latest, art, character_store.load(), root)
        bibles = latest_bibles(snapshot['data'])
        issues = inspect(snapshot)
        for person in people:
            refs = snapshot['references'][person['id']]
            bible = bibles.get(person['id'])
            problem = refs['error'] or ('基準画像が未採用・欠損です。' if not person['primary_reference_id'] or not any(r['id'] == person['primary_reference_id'] and r['state'] == 'present' for r in refs['images']) else
                'バイブルが未確認・古い状態です。' if not bible or bible_stale(bible, latest, person, refs['images']) else '')
            identity = [i['message'] for i in issues if i['character_id'] == person['id'] and i['kind'] == 'identity_ambiguous']
            if problem or identity:
                character_missing.append(person['name']+' / '+person['id']+'：'+(problem or identity[0]))
            else:
                ready_people.add(person['id'])
    if not people:
        character_missing.append('正式な人物設定と確認済みバイブル・基準画像がありません。')
    rows.append(stage('characters','キャラクター設定・バイブル・基準画像',len(ready_people),len(people) or 1,'キャラクター・作風','AIキャラクター制作・一貫性管理',
        '必要な人物を登録し、基準画像とバイブルを確認する',character_missing,stale=bool(people and character_missing),started=bool(people)))
    blocking = {'fixed_conflict','name_id_mismatch','feature_identity_ambiguous','unknown_character_id','direction_stale','speaker_unconfirmed','speaker_direction_mismatch'}
    direction_missing = []
    ready_directions = 0
    for p in panels:
        entry = art['panels'].get(p['key'], {})
        direction = entry.get('direction')
        reasons = [i['message'] for i in issues if (i['page'],i['panel']) == (p['page'],p['panel']) and i['kind'] in blocking]
        if not p['direction']:
            reasons.append('作画指示がありません。')
        elif p['direction_stale']:
            reasons.append('作画指示の脚本版が古い状態です。')
        elif not set(direction['character_ids']) <= ready_people:
            reasons.append('登場人物のバイブル・基準画像を確認してください。')
        if not confirmed:
            reasons.append('脚本が未確定です。')
        p['prompt_ready'] = not reasons
        if reasons:
            direction_missing.append(f"{p['page']}ページ{p['panel']}コマ："+' / '.join(dict.fromkeys(reasons)))
        else:
            ready_directions += 1
    rows.append(stage('directions','作画指示・画像プロンプトの準備',ready_directions,n,'絵コンテ・作画指示','コマ一覧・制作へ',
        '作画指示・人物UUIDを確認してプロンプトを準備する',direction_missing or ([] if n else ['正式脚本が必要です。']),stale=any(p['direction_stale'] for p in panels),started=any(p['direction'] for p in panels)))
    test_images = []
    image_missing = []
    selected_ids = set()
    for person in people:
        selected_ids.add(person['primary_reference_id'])
        for ref in person['references']:
            if 'TEST_ONLY' in ref['original_name']:
                test_images.append(ref['id'])
    ready_images = 0
    linked = {a['image_id']:a['candidate_id'] for a in image_data['adoptions']}
    jobs = {j['candidate_id']:j for j in image_data['jobs']}
    for p in panels:
        entry = art['panels'].get(p['key'], {})
        selected = next((i for i in entry.get('candidates',[]) if i['id'] == entry.get('selected_image_id')),None)
        if selected:
            selected_ids.add(selected['id'])
            job = jobs.get(linked.get(selected['id']))
            if job:
                # バイブル変更も要確認。外見を自動判定したことにはしない。
                expected = [bibles[i]['id'] for i in job['plan']['source']['direction']['character_ids'] if i in bibles]
                p['image_stale'] |= job['plan']['bible_ids'] != expected
                p['stale'] |= p['image_stale']
            if 'TEST_ONLY' in selected['original_name'] or job and job['provider'] == 'mock':
                test_images.append(selected['id'])
        good = selected and not p['stale'] and p['prompt_ready']
        if good:
            ready_images += 1
        else:
            image_missing.append(f"{p['page']}ページ{p['panel']}コマ："+('脚本版・人物・作画設定を再確認してください。' if p['stale'] else '指示・基準画像を準備し、候補画像を明示採用してください。'))
    for identifier in selected_ids:
        job = jobs.get(linked.get(identifier))
        if job and (job['provider'] == 'mock' or 'TEST_ONLY' in (job.get('input') or {}).get('name','')):
            test_images.append(identifier)
    rows.append(stage('images','画像の手動取り込み・比較・明示採用',ready_images,n,'イラスト制作','手動画像制作',
        '未完成コマのプロンプトと基準画像を取得し、生成画像を取り込んで採用する',image_missing or ([] if n else ['正式脚本が必要です。']),stale=any(p['image_stale'] for p in panels),started=any(p['candidates'] for p in panels)))
    fingerprint = production_fingerprint(latest, art, composition, root) if latest else None
    layouts = sum(bool((composition['pages'].get(str(p['page'])) or {}).get('source_version_id') == latest['id'] and composition['pages'][str(p['page'])]['source_page'] == p) for p in latest['story']['pages']) if latest else 0
    png, pdf, output_errors = _export_pages(studio_store, studio, fingerprint, pages)
    page_missing = []
    if layouts != len(pages) or not pages: page_missing.append(f'現在の脚本で保存したページ構成 {layouts}/{len(pages)}。ページ番号順・コマ順を確認して保存してください。')
    if png != pages or not pages: page_missing.append(f'保存済みPNG {len(png)}/{len(pages)}ページ。')
    if pdf != pages or not pages: page_missing.append(f'保存済みPDF {len(pdf)}/{len(pages)}ページ。')
    if ready_images != n or not n: page_missing.append('未採用・古いコマ画像があります。')
    rows.append(stage('pages','漫画ページ編集・PNG・PDF出力',layouts+len(png)+len(pdf),3*len(pages),'完成・書き出し','PNG・PDF書き出し',
        'ページ構成を保存し、全ページのPNGとPDFを生成する',page_missing,stale=bool(page_missing and studio['exports']),started=bool(composition['pages'])))
    proof = evidence(directory, project.id)
    audio_total = len(voice.plan(voice_data)['rows']) if latest else 0
    audio_done = proof['audio_done'] if proof else 0
    audio_missing = []
    if not proof: audio_missing.append('音声・字幕・動画の実ファイルを検証してください。')
    else:
        if audio_done != audio_total: audio_missing.append(f'現在の脚本の音声 {audio_done}/{audio_total}発話。')
        if proof['audio_reviewed'] != audio_total: audio_missing.append('人間が未確認の音声があります。')
        if proof['mock_audio']: audio_missing.append('無料モックは確認用トーンです。日本語の完成音声は未確認です。')
        if not proof['timeline_id']: audio_missing.append('最新のタイムライン・SRTを保存し、字幕の一致を確認してください。')
    rows.append(stage('voice','音声・字幕の生成と確認',audio_done,audio_total or 1,'完成・書き出し','音声制作・字幕',
        '無料モックで生成し、音声を確認してタイムライン・SRTを保存・検証する',audio_missing,stale=bool(audio_missing and voice_data['assets']),started=bool(voice_data['jobs'])))
    video_done = len(proof['export_ids']) if proof else 0
    video_missing = [] if video_done == len(FORMATS) else ['最新の音声・字幕と編集計画に一致する3形式MP4を生成・検証してください。']
    rows.append(stage('video','YouTube・TikTok・ShortsのMP4出力',video_done,len(FORMATS),'完成・書き出し','動画制作・MP4出力',
        '3形式の動画計画を保存し、FFmpegで出力する',video_missing,stale=bool(video_missing and video['exports']),started=bool(video['plans'])))
    context = load_context(directory, project.id, cached=False)
    reviews = ReviewStore(directory, project.id).load()
    quality = summary(context, reviews)
    checks = [
        ('人物設定・基準画像',rows[2]['state'] == '完了','characters'),
        ('全コマの正式採用画像',rows[4]['state'] == '完了','images'),
        ('ページ構成・順序',bool(pages) and layouts == len(pages),'pages'),
        ('セリフ・字幕・音声同期',bool(proof and proof['timeline_id'] and proof['audio_reviewed'] == audio_total and not proof['mock_audio']),'voice'),
        ('PNG・PDF出力',bool(pages) and png == pdf == pages,'pages'),
        ('3形式MP4出力',video_done == len(FORMATS),'video'),
        ('テスト画像・確認用音声なし',not test_images and not (proof and proof['mock_audio']) and bool(n and proof),'images'),
        ('品質確認・未解決タスク',bool(pages) and quality['finals'] == len(pages) and not quality['tasks'] and not quality['unconfirmed_ai'],'quality'),
        ('保存途中・実行中ジョブの解消',not recoveries,'quality'),
    ]
    token = digest(dict(project={k:project.to_dict()[k] for k in ('title','genre','synopsis','page_count')},stages=rows, media=media_token(directory, project.id), outputs=studio['exports'],
        output_files={e['path']:signature(root/e['path']) for e in studio['exports'] if e.get('path')}, review=reviews, checks=checks))
    final = next((e for e in reversed(video['events']) if e['kind'] == 'production_final_review' and e['token'] == token
        and set(e['checks']) == set(MANUAL_DELIVERY) and all(v is True for v in e['checks'].values())),None)
    checks.append(('素材権利・完成品の人間による最終確認',bool(final),'quality'))
    final_missing = [label+'：未完了・未確認' for label,passed,_ in checks if not passed]
    rows.append(stage('quality','完成品の最終品質チェック',sum(ok for _,ok,_ in checks),len(checks),'完成・書き出し','品質チェック・修正管理',
        '品質チェックを終え、ダッシュボードで3形式の完成品と素材権利を明示確認する',final_missing,
        stale=bool(final_missing and (reviews['scans'] or video['exports'])),started=bool(reviews['scans'])))
    return dict(rows=rows, panels=panels, next_step=next((r for r in rows if r['state'] != '完了'),None),
        recoveries=recoveries, checks=checks, token=token, final_review=final, proof=proof,
        output_errors=output_errors, test_images=list(set(test_images)), quality=quality,
        updated=[r['label'] for r in rows if r['state'] == '要確認'])


def confirm_delivery(project, directory, checks, note, *, expected_revision, expected_token):
    if set(checks) != set(MANUAL_DELIVERY) or any(v is not True for v in checks.values()) or not isinstance(note,str) or not note.strip() or len(note) > 4000:
        raise ValueError('すべての完成品・権利項目と確認メモを入力してください。')
    store = VideoService(directory, project.id).store
    with store.lock():
        data = store.checked(expected_revision)
        current = dashboard(project, directory)
        if current['token'] != expected_token or current['recoveries']:
            raise StorageError('制作データか保存状態が変わりました。最新の完成品を確認してください。')
        if not all(ok for _,ok,_ in current['checks'][:-1]) or any(r['state'] != '完了' for r in current['rows'][:-1]):
            raise ValueError('未完了の工程・品質確認・テスト素材があります。完成確認は保存できません。')
        if current['final_review']:
            return current['final_review']
        store.save(data,'production_final_review',token=expected_token,checks=checks,note=note.strip())
        return data['events'][-1]
