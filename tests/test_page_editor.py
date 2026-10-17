import copy
import json
from io import BytesIO
from zipfile import ZipFile
from uuid import uuid4

import pytest
from PIL import Image
from pypdf import PdfReader

from manga.page_dialogue import split_dialogue
from manga.page_images import place_image,placement_for
from manga.page_layout import default_page,default_bubble,validate_page,pixel_frames
from manga.bubble_layout import proposed_bubbles
from manga.japanese_text import find_japanese_font,fit_text
from manga.japanese_wrap import NO_START,NO_END
from manga.page_render import render_page,render_cell,RenderCache
from manga.page_exports import export_book,export_vertical,page_png
from manga.page_storage import PageStore
from manga.page_editor import canvas_event,new_draft,event_token
from manga.storage import StorageError
from page_editor_fixtures import create_example


@pytest.fixture
def example(tmp_path):
    return create_example(tmp_path)


def test_speaker_split_repeated_speaker_and_original_preservation():
    raw='悠真：「あのさ、美咲。今日の帰り――」\n美咲：「ねえ、悠真。先に話してもいい？」\n悠真：「うん」'
    result=split_dialogue(raw)
    assert [u.speaker for u in result]==['悠真','美咲','悠真']
    assert [u.text for u in result]==['あのさ、美咲。今日の帰り――','ねえ、悠真。先に話してもいい？','うん']
    assert ''.join(u.raw for u in result)==raw
    assert not any(u.needs_review for u in result)


@pytest.mark.parametrize('raw',['話者のない文章。','地の文\n悠真：「こんにちは」','悠真：','悠真：「途中','悠真：「あ」 美咲：「い」'])
def test_ambiguous_dialogue_kept_verbatim_for_manual_review(raw):
    result=split_dialogue(raw)
    assert len(result)==1 and result[0].needs_review
    assert result[0].text==result[0].raw==raw
    assert result[0].speaker==''


def test_new_example_auto_sizes_and_splits_without_changing_originals(example):
    project,latest,images,store=example
    data=store.load();page=latest['story']['pages'][0]
    original={p:p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    config=default_page(project.id,page,latest['id'],data['settings'])
    assert validate_page(config)
    assert len(config['bubbles'])==4
    assert [b['speaker'] for b in config['bubbles'] if b['panel']==2]==['悠真','美咲']
    assert not any('悠真：' in b['text'] or '美咲：' in b['text'] for b in config['bubbles'])
    narration=config['bubbles'][0]
    assert narration['kind']=='narration' and narration['shape']=='rectangle' and not narration['tail']
    assert narration['box']['width']*narration['box']['height']<.18
    assert narration['opacity']<1 and not narration['outline']
    p2=[b for b in config['bubbles'] if b['panel']==2]
    assert p2[0]['box']['x']>p2[1]['box']['x']
    data['pages']['1']=config
    rendered=render_page(page,latest['id'],images.load(),data,images,find_japanese_font())
    assert not rendered.errors,rendered.errors
    with Image.open(BytesIO(rendered.png)) as image:
        for frame in pixel_frames(page,config,data['settings']):
            # コマ中央・下端に、contain由来の白い帯がない。
            assert image.getpixel((frame['x']+frame['width']//2,frame['y']+frame['height']-10)) != (255,255,255)
    assert all(p.read_bytes()==content for p,content in original.items())


def test_legacy_page_not_migrated_on_read_or_other_edits(example):
    project,latest,images,store=example
    page=latest['story']['pages'][0]
    old=dict(template='hero_top',frames=None,source_page=page,source_version_id=latest['id'],
        bubbles=[default_bubble(project.id,1,p['panel'],source) for p in page['panels'] for source in ('dialogue','narration')])
    assert placement_for(old,1)['mode']=='contain'
    store.save_page(1,old,story_version_id=latest['id'],expected_revision=1)
    before=store.path.read_bytes()
    loaded=PageStore(store.root.parent,project.id).load()
    assert loaded['pages']['1']==old
    assert store.path.read_bytes()==before
    assert len(loaded['pages']['1']['bubbles'])==6
    assert loaded['pages']['1']['bubbles'][2]['text'] is None
    proposed,_=proposed_bubbles(project.id,page,pixel_frames(page,old,loaded['settings']),loaded['settings'])
    assert len(proposed)==4 and store.path.read_bytes()==before
    store.save_settings(dict(loaded['settings'],dpi=72),expected_revision=2)
    assert store.load()['pages']['1']==old


@pytest.mark.parametrize('placement',[dict(mode='cover',x=0,y=.5,zoom=1),dict(mode='cover',x=1,y=.5,zoom=2),dict(mode='contain',x=.5,y=.5,zoom=4)])
def test_image_crop_or_contain_keeps_source_and_focus(placement):
    source=Image.new('RGB',(600,100),'red')
    for x in range(300,600):
        for y in range(100):source.putpixel((x,y),(0,0,255))
    before=source.tobytes()
    image,position=place_image(source,(100,100),placement)
    if placement['mode']=='cover':
        assert image.size==(100,100) and position==(0,0)
        assert image.getpixel((50,50)) == ((255,0,0) if placement['x']==0 else (0,0,255))
    else:assert image.size==(100,17)  # 倍率4でも全体を表示する。
    assert source.tobytes()==before


@pytest.mark.parametrize('field,value',[('zoom',0),('zoom',float('nan')),('x',2),('y',True),('mode','bad')])
def test_reject_invalid_placement(example,field,value):
    project,latest,_,store=example
    config=default_page(project.id,latest['story']['pages'][0],latest['id'])
    config['image_placements']['1'][field]=value
    with pytest.raises(ValueError):validate_page(config)


@pytest.mark.parametrize('flow',['horizontal','vertical'])
def test_kinsoku_preserves_text_and_avoids_automatic_bad_breaks(flow):
    text='今日は「最後の約束」を話そう。ずっと、待っていたんだ！'
    _,font,(content,step)=fit_text(text,150,160,flow,find_japanese_font(),24,18)
    lines=content if flow=='vertical' else [list(line) for line in content]
    assert ''.join(''.join(line) for line in lines)==text
    assert len(lines)>1
    assert all(not line or line[0][0] not in NO_START for line in lines[1:])
    assert all(not line or line[-1][-1] not in NO_END for line in lines[:-1])


def test_preview_png_pdf_and_vertical_share_the_same_cells(example):
    project,latest,images,store=example
    page=latest['story']['pages'][0];data=store.load()
    config=default_page(project.id,page,latest['id'],data['settings'])
    config['image_placements']['1'].update(x=.1,y=.4,zoom=1.25)
    config['image_placements']['2'].update(x=.8,zoom=1.15)
    data['pages']['1']=config;art=images.load();font=find_japanese_font();cache=RenderCache()
    full=render_page(page,latest['id'],art,data,images,font,cache)
    assert not full.errors,full.errors
    small=render_page(page,latest['id'],art,data,images,font,cache,width=600)
    with Image.open(BytesIO(full.png)) as image:
        expected=image.resize((600,900),Image.Resampling.LANCZOS)
        assert Image.open(BytesIO(small.png)).tobytes()==expected.tobytes()
    assert page_png(full)==full.png
    pdf,_=export_book(latest['story'],latest['id'],art,data,images,font,'pdf',cache)
    reader=PdfReader(BytesIO(pdf));assert len(reader.pages)==1
    pdf_image=next(iter(reader.pages[0].images)).image
    assert pdf_image.size==(1080,1620)
    from PIL import ImageChops,ImageStat
    difference=ImageStat.Stat(ImageChops.difference(pdf_image.convert("RGB"),Image.open(BytesIO(full.png)).convert("RGB")))
    assert max(difference.mean)<3  # PDFのJPEG圧縮差だけを許容し、配置・本文は同じ。
    sns,_=export_vertical([page],latest['id'],art,data,images,font,600,cache)
    with ZipFile(BytesIO(sns)) as archive:
        order=json.loads(archive.read('reading_order.json'))
        for entry,panel,frame in zip(order,page['panels'],pixel_frames(page,config,data['settings'])):
            assert entry['panel']==panel['panel']
            png,errors,_=render_cell(1,panel,config,art,images,(frame['width'],frame['height']),1080,data['settings'],font,cache)
            assert not errors
            expected=Image.open(BytesIO(png)).resize((600,entry['height']),Image.Resampling.LANCZOS)
            output=Image.open(BytesIO(archive.read(entry['file']))).crop((0,entry['y'],600,entry['y']+entry['height']))
            assert output.tobytes()==expected.tobytes()


def test_crop_cache_invalidates_only_changed_panel(example):
    project,latest,images,store=example;data=store.load();page=latest['story']['pages'][0]
    data['pages']['1']=default_page(project.id,page,latest['id']);cache=RenderCache();font=find_japanese_font()
    render_page(page,latest['id'],images.load(),data,images,font,cache)
    assert cache.misses==3
    data['pages']['1']['image_placements']['2']['zoom']=1.8
    render_page(page,latest['id'],images.load(),data,images,font,cache)
    assert cache.misses==4 and cache.hits==2


def test_history_restore_and_failure_protects_old_edits(example,monkeypatch):
    project,latest,images,store=example;page=latest['story']['pages'][0]
    config=default_page(project.id,page,latest['id'])
    store.save_page(1,config,story_version_id=latest['id'],expected_revision=1)
    changed=copy.deepcopy(config);changed['image_placements']['1']['zoom']=2
    store.save_page(1,changed,story_version_id=latest['id'],expected_revision=2)
    store.restore_page(1,2,story_version_id=latest['id'],expected_revision=3)
    assert store.load()['pages']['1']==config
    assert store.load()['history'][-1]['before']==changed
    before=store.path.read_bytes()
    with pytest.raises(StorageError):store.restore_page(1,2,story_version_id=latest['id'],expected_revision=2)
    assert store.path.read_bytes()==before
    with pytest.raises(ValueError):store.restore_page(1,0,story_version_id=latest['id'],expected_revision=4)


def test_canvas_events_validate_targets_rectangles_and_version(example):
    project,latest,_,store=example
    config=default_page(project.id,latest['story']['pages'][0],latest['id']);draft=new_draft(config,1,latest['id']);token=event_token(draft)
    event=dict(type='move',id=config['bubbles'][0]['id'],box=dict(x=.2,y=.1,width=.5,height=.2),token=token,event_id='one')
    changed,selected=canvas_event(config,event,token)
    assert changed['bubbles'][0]['box']==event['box'] and selected==event['id']
    assert config['bubbles'][0]['box']!=event['box']
    for values in (dict(token='old'),dict(id=str(uuid4())),dict(box=dict(x=.9,y=0,width=.5,height=.5)),dict(box=dict(x=float('nan'),y=0,width=.5,height=.5))):
        with pytest.raises(ValueError):canvas_event(config,dict(event,**values),token)


def test_many_utterances_never_truncate_original_dialogue(example):
    project,latest,_,store=example
    page=copy.deepcopy(latest['story']['pages'][0])
    page['panels'][0]['dialogue']='\n'.join('悠真：「話をしよう」' for _ in range(61))
    config=default_page(project.id,page,latest['id'])
    assert validate_page(config) and len(config['bubbles'])<=60
    assert any(b['text']==page['panels'][0]['dialogue'] and b['needs_review'] for b in config['bubbles'])


def test_partial_canvas_payload_is_rejected_without_modifying_page(example):
    project,latest,_,_=example
    config=default_page(project.id,latest['story']['pages'][0],latest['id']);before=copy.deepcopy(config)
    with pytest.raises(ValueError,match='欠け'):
        canvas_event(config,dict(type='move',id=config['bubbles'][0]['id'],token='current',event_id='partial',box={}), 'current')
    assert config==before


@pytest.mark.parametrize('raw',['\n悠真：「話そう」\n美咲：「うん」','  悠真:「話そう」\n'])
def test_speaker_split_keeps_leading_whitespace_in_source(raw):
    result=split_dialogue(raw)
    assert ''.join(u.raw for u in result)==raw
    assert result[0].speaker=='悠真' and not result[0].needs_review


def test_narration_opacity_affects_background_only():
    from manga.bubble_render import draw_bubble
    font=find_japanese_font()
    source=Image.new('RGB',(500,500),(20,80,140))
    bubble=dict(box=dict(x=.1,y=.1,width=.5,height=.2),shape='rectangle',tail=False,flow='horizontal',font_scale=.025,min_font_scale=.02,opacity=.5,outline=False,background='#ffffff',text_layout='japanese_v2')
    draw_bubble(source,bubble,'あ',1080,0,font)
    pixel=source.getpixel((52,52))
    assert all(old<new<255 for old,new in zip((20,80,140),pixel))
    assert source.getpixel((5,5))==(20,80,140)
    assert any(pixel==(0,0,0) for pixel in source.getdata())  # 文字本体の黒は透過させない。
