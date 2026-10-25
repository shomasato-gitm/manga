"""7ステップの表示を保存済み素材から導出する。進捗の正本を増やさない。"""
TITLES = (
    'コマの内容を確認', '作画指示を入力・保存',
    '画像生成プロンプト・基準画像・対象確認票を取得',
    'ChatGPT等で画像を制作（外部での手動操作）', '画像と対象確認票を取り込む',
    '候補画像を比較・確認', '正式採用して次のコマへ',
)


def step_states(row, data, material_token, *, finalized, prepared, dirty=False):
    jobs = {j['id']: j for j in data['jobs'] if j['plan']['kind'] == 'panel'
            and (j['plan']['page'], j['plan']['panel']) == (row['page'], row['panel'])}
    all_images = [c for c in data['candidates'] if c['job_id'] in jobs]
    images = [c for c in all_images if material_token and jobs[c['job_id']]['plan']['material_token'] == material_token
              and c['provider'] == 'manual' and not c['mock']]
    reviews = [r for r in data['reviews'] if any(r['candidate_id'] == c['id'] and r['sha256'] == c['sha256']
               and r['material_token'] == material_token for c in images)]
    adopted = row['selected'] and not row['stale'] and row['prompt_ready']
    states = ['完了' if finalized else '要再確認',
              '要再確認' if row['direction_stale'] or dirty and row['direction'] else '完了' if row['direction'] else '未完了',
              '完了' if prepared else '要再確認' if all_images and not material_token else '未完了',
              '完了' if images else '要再確認' if all_images else '未完了',
              '完了' if images else '要再確認' if all_images else '未完了',
              '完了' if reviews else '要再確認' if all_images and not images else '未完了',
              '完了' if adopted else '要再確認' if row['selected'] or row['stale'] else '未完了']
    # モックだけの採用・旧経路の画像から、対象確認票付き実画像の制作完了は推定しない。
    if adopted and not images:
        states[-1] = '要再確認'
    return [dict(number=i+1, title=title, state=state) for i, (title, state) in enumerate(zip(TITLES, states))]


def guidance(error, *, page=None, panel=None):
    """不足・必要性・次の操作を一つの文脈で伝える。技術的例外は呼び出し元で折りたたむ。"""
    message = str(error)
    target = f'{page}ページ{panel}コマ目の' if page is not None else 'この対象の'
    if '作画指示' in message and ('最新' in message or '古い' in message):
        return target+'作画指示が未保存、または現在の脚本版と一致しません。プロンプトには保存済みの最新指示が必要です。下の「STEP 2：作画指示を入力・保存」で確認・保存してください。'
    if '確認票' in message:
        return '画像と対象確認票がこのコマに一致しません。取り違えを防ぐため、STEP 3でこのコマの確認票を取得し、画像の制作対象と照合してから取り込んでください。'
    if '確定' in message or '脚本' in message and '版' not in message:
        return '最新脚本の確定が不足しています。古い脚本からの制作を防ぐため、「企画・脚本」の「比較・脚本確定」で内容を確認・確定してから、この画面に戻ってください。'
    if any(word in message for word in ('人物', 'バイブル', '参照画像', '基準画像', '話者', '同名')):
        return '人物・基準画像・バイブルの確認が不足しています。人物の取り違えを防ぐため、下の案内で不足項目を確認し、「人物・基準画像・バイブルを確認する」から修正してください。'
    if any(word in message for word in ('変わ', '変更', '更新')):
        return '制作元の素材または版が更新されています。古い確認票での取り込みを防ぐため、作画指示と人物を再確認し、STEP 3で準備し直してください。'
    if '確認票' in message or '一致しません' in message:
        return '画像と対象確認票がこのコマに一致しません。取り違えを防ぐため、STEP 3でこのコマの確認票を取得し、画像の制作対象と照合してから取り込んでください。'
    return '画像制作の入力または保存状態を確認できません。安全に保存するため処理を停止しました。入力形式・保存先・空き容量を確認して再試行してください。保存済みの画像と履歴は保持しています。'
