"""既存の話者分割を利用し、曖昧な人物を推測せず確認へ回す。"""
from uuid import UUID, uuid5
import unicodedata

from manga.page_dialogue import split_dialogue
from manga.voice_models import digest, narrator_id, speech_text


def extract_utterances(project_id,story,artwork,edits=None):
    edits = edits or {}
    by_name = {}
    cast = {c['id']:c for c in artwork['characters'] if not c['deleted_at']}
    for character in cast.values():
        by_name.setdefault(unicodedata.normalize('NFC',character['name']).strip(),[]).append(character['id'])
    narrator = narrator_id(project_id)
    result = []
    for page in story['pages']:
        for panel in page['panels']:
            parts = [('dialogue',i,u.speaker,u.text,u.needs_review) for i,u in enumerate(split_dialogue(panel['dialogue']))]
            if panel['narration'].strip():
                parts.insert(0,('narration',0,'ナレーター',panel['narration'],False))
            for kind,index,name,text,ambiguous in parts:
                identifier = str(uuid5(UUID(project_id),f"voice:{page['page']}:{panel['panel']}:{kind}:{index}"))
                matches = by_name.get(unicodedata.normalize('NFC',name).strip(),[])
                speaker = narrator if kind=='narration' else matches[0] if len(matches)==1 and not ambiguous else None
                warnings = []
                if speaker is None:warnings.append('話者を特定できません。人物と読み上げ文を確認してください。')
                item = dict(id=identifier,page=page['page'],panel=panel['panel'],kind=kind,
                    speaker_name=name,speaker_id=speaker,text=text,spoken_text=text,text_hash=digest(text),warnings=warnings)
                edit = edits.get(identifier)
                if edit:
                    if edit['source_text_hash']!=item['text_hash']:
                        warnings.append('元のセリフが変更されました。以前の読み・話者指定を確認し直してください。')
                    else:
                        item.update(speaker_id=edit['speaker_id'],spoken_text=edit['spoken_text'],emotion=edit['emotion'])
                        warnings.clear()
                if item['speaker_id'] not in (*cast,narrator):
                    warnings.append('指定された人物IDが現在の設定にありません。')
                try:speech_text(item['spoken_text'])
                except ValueError as error:warnings.append(str(error))
                item['needs_review'] = bool(warnings)
                result.append(item)
    return result
