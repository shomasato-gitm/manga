"""音声制作の設定・計画。音声処理と出力は独立モジュールへ分離する。"""
import copy
import os

from manga.artwork_storage import ArtworkStore
from manga.editorial_storage import EditorialStore
from manga.story_storage import StoryStore
from manga.voice_audio import inspect_wav
from manga.voice_models import digest, narrator_id, speech_text, voice_settings, cache_key, EMOTIONS
from manga.voice_script import extract_utterances
from manga.voice_storage import VoiceStore


class VoiceService:
    def __init__(self,directory,project_id,*,polly=None,voicevox=None):
        self.directory=directory;self.project_id=project_id
        self.store=VoiceStore(directory,project_id)
        self.stories=StoryStore(directory,project_id)
        self.artwork=ArtworkStore(directory,project_id)
        self.editorial=EditorialStore(directory,project_id)
        self.polly=polly
        self.voicevox=voicevox

    def source(self):
        history=self.stories.load_history()
        if not history:raise ValueError('先にストーリーJSONを保存してください。')
        latest=history[-1]
        artwork=self.artwork.load()
        confirmed=any(f['story_version_id']==latest['id'] and f['source_story']==latest['story'] for f in self.editorial.load()['finalizations'])
        return latest,artwork,confirmed

    def speakers(self,artwork=None):
        artwork=artwork or self.artwork.load()
        return {**{c['id']:c['name'] for c in artwork['characters'] if not c['deleted_at']},narrator_id(self.project_id):'ナレーター'}

    def plan(self,data=None):
        data=data if data is not None else self.store.load()
        latest,artwork,confirmed=self.source()
        rows=extract_utterances(self.project_id,latest['story'],artwork,data['edits'])
        assets={a['id']:a for a in data['assets']}
        for row in rows:
            settings=data['voices'].get(row['speaker_id'])
            if not settings:
                row['warnings'].append('この話者の声設定を保存してください。')
                row.update(needs_review=True,settings=None,cache_key=None,asset=None,status='要確認');continue
            settings=dict(settings,emotion=row.get('emotion',settings['emotion']) if settings['provider'] in ('mock','aws-polly') else 'normal')
            if row['needs_review']:
                row.update(settings=settings,cache_key=None,asset=None,status='要確認');continue
            row['settings']=settings;row['cache_key']=cache_key(row,settings)
            chosen=assets.get(data['assignments'].get(row['id']))
            if settings['provider'] in ('voicevox','manual-wav'):
                from manga.voice_local import matches
                review=data['audio_reviews'].get(chosen['id']) if chosen else None
                if not chosen or not matches(chosen,row,latest['id']) or not review or review['sha256']!=chosen['info']['sha256']:
                    chosen=None
            elif not chosen or chosen['cache_key']!=row['cache_key']:
                chosen=next((a for a in reversed(data['assets']) if a['cache_key']==row['cache_key']),None)
            row['asset']=chosen
            pending=next((j for j in reversed(data['jobs']) if j['cache_key']==row['cache_key'] and j['state'] not in ('ready','not_sent','closed')),None)
            if chosen:
                path=self.store.safe_path(chosen['path'])
                row['status']='再利用可' if path.is_file() else '音声欠損・要確認'
            else:row['status']='請求・保存の確認待ち' if pending else '未生成'
            if not chosen and settings['provider'] in ('voicevox','manual-wav'):
                row['status']='処理中・保存の復旧待ち' if pending else '候補あり・試聴／正式割り当て待ち' if any(matches(a,row,latest['id']) for a in data['assets'] if a['settings']['provider'] in ('voicevox','manual-wav')) else '音声の準備待ち'
            row['needs_review']=bool(row['warnings'])
        return dict(version=latest,artwork=artwork,confirmed=confirmed,rows=rows,
                    token=digest(dict(version_id=latest['id'],story=latest['story'],speakers=self.speakers(artwork),settings_revision=data['settings_revision'])))

    def save_voices(self,voices,*,expected_revision):
        with self.store.locked(sources=True):
            data=self.store.checked(expected_revision)
            active=self.speakers()
            if not isinstance(voices,dict) or not set(voices)<=set(active):raise ValueError('現在の人物ID・ナレーターIDを指定してください。')
            normalized={key:voice_settings(value) for key,value in voices.items()}
            before=copy.deepcopy(data['voices'])
            data['voices'].update(normalized);data['settings_revision']+=1
            self.store.save(data,'voices',before=before)

    def save_edit(self,utterance_id,speaker_id,spoken_text,emotion,*,expected_revision,source_token):
        with self.store.locked(sources=True):
            data=self.store.checked(expected_revision)
            plan=self.plan(data)
            if plan['token']!=source_token:raise ValueError('元の脚本・声設定が変わりました。読みと話者を確認し直してください。')
            row=next((r for r in plan['rows'] if r['id']==utterance_id),None)
            if row is None or speaker_id not in self.speakers(plan['artwork']) or emotion not in EMOTIONS:raise ValueError('発話・話者・感情が不正です。')
            edit=dict(source_text_hash=row['text_hash'],speaker_id=speaker_id,spoken_text=speech_text(spoken_text),emotion=emotion)
            before=data['edits'].get(utterance_id)
            data['edits'][utterance_id]=edit;data['settings_revision']+=1
            self.store.save(data,'edit',before=before,utterance_id=utterance_id)

    def save_pricing(self,card,*,expected_revision):
        from manga.voice_pricing import price_quote
        if not isinstance(card,dict) or not isinstance(card.get('engines'),dict):raise ValueError('公式料金設定はenginesを含むJSONオブジェクトで指定してください。')
        for engine in card.get('engines',{}):
            if engine not in ('standard','neural'):raise ValueError('未対応のengineです。')
            quote=price_quote(card,engine)
            quote.execution_evidence('aws-polly',engine,dict(max_input_tokens=0,max_output_tokens=0,max_items=1,retry_limit=0))
        if not card.get('engines'):raise ValueError('少なくとも1つのengine料金が必要です。')
        with self.store.locked():
            data=self.store.checked(expected_revision)
            before=copy.deepcopy(data['pricing']);data['pricing']=copy.deepcopy(card);data['settings_revision']+=1
            self.store.save(data,'pricing',before=before)

    def review_audio(self,asset_id,*,expected_revision):
        from manga.models import now_iso
        with self.store.locked(sources=True):
            data=self.store.checked(expected_revision)
            asset=next((a for a in data['assets'] if a['id']==asset_id),None)
            if not asset:raise ValueError('確認する音声がありません。')
            self.audio_bytes(asset)
            if asset['settings']['provider'] in ('voicevox','manual-wav'):
                from manga.voice_local import matches
                plan=self.plan(data)
                if not any(matches(asset,r,plan['version']['id']) for r in plan['rows']):raise ValueError('脚本・話者・声設定が変更されています。現在の発話で候補を用意してください。')
                if not asset['provenance']['rights']['confirmed']:raise ValueError('ソフトと音声固有の利用条件が未確認です。確認して新しい候補として取り込んでください。')
            data['audio_reviews'][asset_id]=dict(sha256=asset['info']['sha256'],confirmed_at=now_iso())
            self.store.save(data,'audio_review',asset_id=asset_id)

    def select_asset(self,utterance_id,asset_id,*,expected_revision):
        with self.store.locked(sources=True):
            data=self.store.checked(expected_revision);plan=self.plan(data)
            row=next((r for r in plan['rows'] if r['id']==utterance_id),None)
            asset=next((a for a in data['assets'] if a['id']==asset_id),None)
            if not row or not asset or row['cache_key']!=asset['cache_key']:raise ValueError('現在の発話・声設定と音声候補が一致しません。')
            if asset['settings']['provider'] in ('voicevox','manual-wav'):
                from manga.voice_local import matches
                review=data['audio_reviews'].get(asset_id)
                if not matches(asset,row,plan['version']['id']) or not review or review['sha256']!=asset['info']['sha256']:
                    raise ValueError('現在の対象と一致する候補を試聴・確認してから、別操作で正式割り当てしてください。')
            self.audio_bytes(asset)
            before=data['assignments'].get(utterance_id);data['assignments'][utterance_id]=asset_id
            self.store.save(data,'audio_selection',before=before,utterance_id=utterance_id,asset_id=asset_id)

    def audio_bytes(self,asset):
        content=self.store.read_blob(asset['path'])
        info=inspect_wav(content)
        if any(info[key]!=asset['info'][key] for key in ('sha256','frames','sample_rate','channels','duration_ms')):
            raise ValueError('保存済み音声の内容が変わっています。元ファイルを削除せず、明示的な再生成を確認してください。')
        if asset['settings']['provider'] in ('voicevox','manual-wav'):
            from manga.voice_local import original_bytes
            original_bytes(self,asset)
        return content

    def voice_target(self,utterance_id):
        from manga.voice_local import target_for
        return target_for(self,utterance_id)

    def local_candidates(self,utterance_id):
        from manga.voice_local import candidates
        return candidates(self,utterance_id)

    def import_wav(self,utterance_id,content,*,target,rights,expected_revision):
        from manga.voice_local import prepare
        return prepare(self,utterance_id,content=content,target=target,rights=rights,expected_revision=expected_revision)

    def preview_voicevox(self,utterance_id,*,target,rights,expected_revision,consent=False,regenerate=False):
        from manga.voice_local import prepare
        return prepare(self,utterance_id,target=target,rights=rights,expected_revision=expected_revision,consent=consent,regenerate=regenerate)

    def estimate(self,ids,*,regenerate=False):
        from decimal import Decimal
        from manga.voice_polly import polly_ssml
        from manga.voice_pricing import price_quote, usage_limits
        data=self.store.load();plan=self.plan(data)
        if not ids or len(ids)!=len(set(ids)):raise ValueError('生成する発話を重複なく選択してください。')
        selected=[row for row in plan['rows'] if row['id'] in ids]
        if len(selected)!=len(ids):raise ValueError('対象の発話が現在の脚本にありません。')
        total=Decimal('0');calls=0;seen=set()
        for row in selected:
            if row['needs_review']:raise ValueError('話者・読み・声設定の要確認項目を解決してください。')
            if row['settings']['provider'] in ('voicevox','manual-wav'):
                raise ValueError('VOICEVOX・WAV取り込みは1発話ずつ「日本語音声の準備」で操作してください。自動処理は実行しません。')
            if row['asset'] and not regenerate:continue
            if not regenerate and row['cache_key'] in seen:continue
            seen.add(row['cache_key'])
            if row['settings']['provider']=='aws-polly':
                quote=price_quote(data['pricing'],row['settings']['engine'])
                limits=usage_limits(polly_ssml(row['spoken_text'],row['settings']))
                evidence=quote.execution_evidence('aws-polly',row['settings']['engine'],limits)
                total+=Decimal(evidence['computed_max_jpy']);calls+=1
        return dict(max_jpy=total,paid_calls=calls,token=plan['token'],settings_revision=data['settings_revision'],
                    ids=list(ids),regenerate=regenerate,approval_key=digest(dict(token=plan['token'],ids=list(ids),regenerate=regenerate,total=str(total))))

    def generate(self,ids,*,regenerate=False,approval=None,progress=None):
        from manga.voice_jobs import generate
        return generate(self,ids,regenerate=regenerate,approval=approval,progress=progress)

    def recover(self,job_id):
        from manga.voice_jobs import recover
        return recover(self,job_id)

    def close_resolved_job(self,job_id,*,confirmed=False):
        from manga.voice_jobs import close_resolved_job
        return close_resolved_job(self,job_id,confirmed=confirmed)

    @staticmethod
    def paid_enabled():
        return os.environ.get('MANGA_ENABLE_PAID_TTS')=='1'
