"""Pictures contain the table, with no duplicated message headings or counters."""
from collections import Counter
import io
from PIL import Image, ImageDraw

from app.table_images import render_pages


def test_paginated_table_contains_every_cell_and_only_column_headers_and_rows(monkeypatch):
    drawn=[]
    original=ImageDraw.ImageDraw.text

    def capture(draw,xy,text,*args,**kwargs):
        drawn.append(str(text))
        return original(draw,xy,text,*args,**kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    headers=['姓名','性别','名额']
    rows=[[f'成员{i}','女','正式'] for i in range(45)]
    pages=list(render_pages(headers,rows))
    assert len(pages)==3
    assert Counter(drawn)==Counter(headers*3+[cell for row in rows for cell in row])
    for blob in pages:
        with Image.open(io.BytesIO(blob)) as image:
            assert image.format=='PNG' and image.mode=='RGB'
            assert image.getpixel((21,21))==(25,118,97), 'Column headers start near the top'


def test_wrapped_guest_names_remain_complete_without_title_space(monkeypatch):
    drawn=[]
    original=ImageDraw.ImageDraw.text

    def capture(draw,xy,text,*args,**kwargs):
        drawn.append(str(text))
        return original(draw,xy,text,*args,**kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    name='一位姓名较长的同行（报名人的真实姓名代报名）'
    pages=list(render_pages(['姓名'],[[name]]))
    assert len(pages)==1 and ''.join(drawn[1:])==name
    with Image.open(io.BytesIO(pages[0])) as image:
        assert image.width<=380 and image.height>156
