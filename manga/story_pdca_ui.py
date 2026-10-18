"""最新評価から改善・再評価を案内し、実差分と主観評価を比較する専用画面。"""
import pandas as pd

from manga.ui_runtime import ui as st
from manga.editorial_models import CRITERIA, MODES, SUBJECTIVE_NOTICE
from manga.editorial_ui import _evaluation_details, _version_labels
from manga.story_studio_status import (exact_evaluation, lineage, cycle_limit, score_rows,
                                       regression_notes, actual_changes, references_for_version)
from manga.story_studio_ui_common import act, move, request_picker
from manga.story_techniques import stale_references


def pdca_view(service, data, editorial, history, prefix):
    st.info(SUBJECTIVE_NOTICE)
    if not history:
        st.info('詳細脚本を保存すると、評価・改善・再評価を行えます。')
        return
    latest = history[-1]
    evaluation = exact_evaluation(editorial, latest)
    chain = lineage(data, history, latest['id'])
    limit = cycle_limit(data, chain['root_version_id'])
    columns = st.columns(4)
    columns[0].metric('改善サイクル', f"{chain['cycles']} / {limit}")
    columns[1].metric('最新の評価点', f"{evaluation['payload']['total_score']:g}" if evaluation else '未評価')
    columns[2].metric('目標点数', data['policy']['target_score'])
    columns[3].metric('現在の版', f'v{len(history)}')
    st.caption('対象版：'+latest['id']+'。改善・復元後は新しい版を必ず再評価します。')
    if not chain['origin']:
        st.info('この版には概要生成元の記録がありません。既存脚本として評価・改善でき、生成時の参考技法は推測しません。')
    for warning in stale_references(references_for_version(data, history, latest['id']), editorial):st.warning(warning)
    for note in regression_notes(data, editorial, history, latest):st.warning(note)
    settings(service, data, prefix)
    options = None
    if evaluation:
        with st.expander('評価8項目・問題点・改善案'):
            _evaluation_details(evaluation['payload'])
        options = improvement_settings(service, evaluation, latest, prefix)
        if chain['cycles'] >= limit:
            st.warning('改善回数の目安に達しました。追加改善は利用者の明示承認が必要です。')
            approve = st.checkbox('追加改善を行う', key=f"{prefix}_{latest['id']}_extend_approve")
            if approve:
                additional = st.number_input('追加する改善回数', min_value=1, max_value=20, value=1, key=f'{prefix}_additional')
                reason = st.text_input('追加改善の理由（必須）', max_chars=2000, key=f'{prefix}_additional_reason')
                if st.button('追加改善を承認して保存', disabled=not reason.strip()):
                    act(service, lambda: service.extend(additional, reason, expected_revision=data['revision'],expected_version_id=latest['id']), '追加改善を明示承認しました。')
            if st.button('追加せず比較・最終確認へ進む'):
                act(service, lambda: service.pause(expected_revision=data['revision'],expected_version_id=latest['id']), '追加改善せず最終確認へ進む選択を記録しました。', '比較・確定')
        else:
            if st.button('最新評価から改善依頼を作成', type='primary'):
                act(service, lambda: service.create_request('improvement', options=options, evaluation_id=evaluation['id'], expected_revision=data['revision']),
                    '最新評価の問題点・理由・対象を引き継いだ改善依頼を作成しました。')
    if st.button('現在版の評価依頼を作成' if not evaluation else '現在版をもう一度評価依頼', type='primary' if not evaluation else 'secondary'):
        act(service, lambda: service.create_request('evaluation', expected_revision=data['revision']), '現在版の評価依頼を作成しました。')
    request_picker(service, data, history, ('evaluation', 'improvement'), prefix+'_pdca')
    st.caption('意味上の矛盾をアプリが自動理解する機能はありません。点数低下は数値から検出し、矛盾の指摘はAIの未確認提案として人間が確認します。')


def settings(service, data, prefix):
    policy = data['policy']
    tag = f"{prefix}_{policy['target_score']}_{policy['improvement_limit']}"
    with st.expander('PDCAの設定（目標点・改善回数の目安）'):
        score = st.number_input('目標点数 / 100', min_value=0, max_value=100, value=policy['target_score'], key=tag+'_goal')
        count = st.number_input('改善回数の目安', min_value=1, max_value=100, value=policy['improvement_limit'], key=tag+'_limit')
        if st.button('制作PDCAの設定を保存'):
            act(service, lambda: service.set_policy(score, count, expected_revision=data['revision']), 'PDCAの設定を変更履歴付きで保存しました。')


def improvement_settings(service, evaluation, latest, prefix):
    default = service.improvement_options(evaluation, latest['story'])
    key = f"{prefix}_{evaluation['id']}_options"
    with st.expander('改善の詳細設定（通常は最新評価の初期値を使用）'):
        mode = st.radio('改善方式', MODES, key=key+'_mode')
        criteria = st.multiselect('改善したい評価項目', list(CRITERIA), default=default['criteria'], format_func=CRITERIA.get, key=key+'_criteria')
        choices = {}
        for page in latest['story']['pages']:
            choices[f"{page['page']}ページ全体"] = dict(page=page['page'], panel=None)
            for panel in page['panels']:
                choices[f"{page['page']}ページ・{panel['panel']}コマ"] = dict(page=page['page'], panel=panel['panel'])
        initial = [label for label, target in choices.items() if target in default['targets']]
        targets = st.multiselect('改善するページ・コマ', list(choices), default=initial, key=key+'_targets')
        reason = st.text_area('今回の改善理由・メモ', value=default['reason'], max_chars=4000, key=key+'_reason')
        st.caption('詳細設定を変更した場合は、改善依頼を新しく作成してください。作成済みプロンプトを勝手に書き換えることはありません。')
    return dict(mode=mode, criteria=criteria, targets=[choices[t] for t in targets], reason=reason)


def comparison_view(service, data, editorial, history, prefix, navigate):
    st.info(SUBJECTIVE_NOTICE)
    if not history:
        st.info('保存済みのストーリーがありません。')
        return
    labels = _version_labels(history)
    latest = history[-1]
    rows = score_rows(editorial, history)
    chart = pd.DataFrame(rows).set_index('版')
    st.subheader('バージョンごとの評価点')
    st.line_chart(chart[['総合点']])
    with st.expander('8項目の点数変化・評価履歴'):
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.line_chart(chart[list(CRITERIA.values())])
        if st.checkbox('選択版の評価詳細を表示', key=prefix+'_eval_detail'):
            selected_eval_version = st.selectbox('評価詳細を見る版', list(reversed(labels)), format_func=labels.get, key=prefix+'_eval_version')
            version = next(v for v in history if v['id'] == selected_eval_version)
            evaluation = exact_evaluation(editorial, version)
            if evaluation:_evaluation_details(evaluation['payload'])
            else:st.info('この版は未評価です。過去版の点数を流用していません。')
    before_id = st.selectbox('変更前の版', list(labels), index=max(0,len(history)-2), format_func=labels.get, key=f"{prefix}_{latest['id']}_before")
    after_id = st.selectbox('変更後の版', list(reversed(labels)), format_func=labels.get, key=f"{prefix}_{latest['id']}_after")
    before, after = [next(v for v in history if v['id'] == identifier) for identifier in (before_id, after_id)]
    old_eval, new_eval = exact_evaluation(editorial, before), exact_evaluation(editorial, after)
    differences = []
    for key, label in CRITERIA.items():
        old = old_eval['payload']['criteria'][key]['score'] if old_eval else None
        new = new_eval['payload']['criteria'][key]['score'] if new_eval else None
        differences.append({'評価項目':label,'変更前':old,'変更後':new,'得点差':round(new-old,2) if old is not None and new is not None else None})
    st.table(differences)
    with st.expander('変更前後の問題点・改善理由を比較'):
        st.dataframe([{'評価項目':label,'変更前の問題点':old_eval['payload']['criteria'][key]['problems'] if old_eval else '未評価',
                       '変更後の問題点':new_eval['payload']['criteria'][key]['problems'] if new_eval else '未評価',
                       '改善理由':old_eval['payload']['criteria'][key]['reason'] if old_eval else '未評価'} for key,label in CRITERIA.items()],hide_index=True,use_container_width=True)
    reason = (after.get('revision_context') or {}).get('reason')
    if reason:st.write('改善理由：'+reason)
    changes = actual_changes(before, after)
    st.write(f'JSON構造に基づく変更箇所：{len(changes)}件')
    if changes:st.dataframe(changes, hide_index=True, use_container_width=True)
    with st.expander('JSON差分詳細・生成元・改善理由'):
        st.json(changes, expanded=False)
        chain = lineage(data, history, after_id)
        st.json(dict(origin=chain['origin'], revision_context=after.get('revision_context'),
                     references=references_for_version(data, history, after_id)), expanded=False)
    with st.expander('過去版の選択・復元（元の履歴を保持）'):
        restore_id = st.selectbox('復元する過去版', list(reversed(labels)), format_func=labels.get, key=prefix+'_restore')
        if st.button('この過去版を復元', disabled=restore_id==latest['id']):
            act(service, lambda: service.restore(restore_id, expected_revision=data['revision'],expected_version_id=latest['id']), '過去版を新しい版として復元しました。再評価してください。', '評価・改善')
    st.subheader('現在の脚本の最終確認')
    evaluation = exact_evaluation(editorial, latest)
    notes = regression_notes(data, editorial, history, latest)+stale_references(references_for_version(data,history,latest['id']),editorial)
    for note in notes:st.warning(note)
    confirmed = any(f['story_version_id']==latest['id'] and f['source_story']==latest['story'] for f in editorial['finalizations'])
    if confirmed:
        st.success('現在の版で脚本確定済みです。修正した場合は画像・作画指示との整合を再確認してください。')
        if navigate and st.button('キャラクター・作画工程へ進む', type='primary'):
            navigate('キャラクター・作風', 'AI一括生成・採用')
        return
    if not evaluation:
        st.warning('現在の版は未評価です。評価・再評価してから最終確認してください。')
    reviewed = st.checkbox('テーマ・世界観・人物設定・独自性・変更箇所を人間が最終確認した', key=f"{prefix}_{latest['id']}_reviewed")
    remaining = bool(evaluation and evaluation['payload']['total_score'] < data['policy']['target_score']) or bool(notes)
    accept = st.checkbox('目標未達・劣化・要確認事項を把握し、残る課題を承認する', key=f"{prefix}_{latest['id']}_remaining") if remaining else False
    if st.button('現在のストーリーを最終確定', type='primary', disabled=not evaluation or not reviewed or (remaining and not accept)):
        act(service, lambda: service.finalize(expected_revision=data['revision'], expected_version_id=latest['id'],expected_evaluation_id=evaluation['id'],reviewed=reviewed, accept_remaining=accept), '人間の最終確認を記録して脚本を確定しました。')
