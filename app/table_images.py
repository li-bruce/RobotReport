"""Render business tables for clients whose Markdown subset has no table support."""
from datetime import timedelta
from pathlib import Path
import hashlib
import html
import io
import json
import re
import secrets
from PIL import Image, ImageDraw, ImageFont
from .db import stamp, now, connect

FONT = Path(__file__).parent / 'assets' / 'fonts' / 'NotoSansCJKsc-Regular.otf'


def cells(line):
    return [html.unescape(re.sub(r'\\(.)', r'\1', cell.strip())) for cell in re.split(r'(?<!\\)\|', line.strip().strip('|'))]


def table_blocks(text):
    lines = text.splitlines()
    i, title = 0, '羽协活动'
    while i < len(lines):
        if lines[i].startswith('#'):
            title = lines[i].lstrip('# ').strip()
        if (lines[i].lstrip().startswith('|') and i + 1 < len(lines)
                and re.fullmatch(r'\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*', lines[i + 1])):
            start = i
            headers = cells(lines[i])
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                row = cells(lines[i])
                rows.append((row + [''] * len(headers))[:len(headers)])
                i += 1
            yield start, i, title, headers, rows
        else:
            i += 1


def render_pages(headers, rows):
    """Yield table-only RGB PNGs, repeating column headers without dropping rows."""
    if not 1 <= len(headers) <= 10 or len(rows) > 2000:
        raise ValueError('表格行列过多，请缩小查询范围')
    font = ImageFont.truetype(str(FONT), 24)
    widths = [min(340, max(100, int(max(font.getlength(str(r[i])) for r in [headers, *rows]) + 32))) for i in range(len(headers))]
    if sum(widths) > 1460:
        factor = 1460 / sum(widths)
        widths = [max(72, int(w * factor)) for w in widths]
    width = sum(widths) + 40

    def wrap(value, max_width):
        result, current = [], ''
        for char in str(value):
            if char == '\n' or (current and font.getlength(current + char) > max_width):
                result.append(current)
                current = '' if char == '\n' else char
            else:
                current += char
        return result + [current]

    prepared = [[wrap(v, widths[i] - 24) for i, v in enumerate(row)] for row in [headers, *rows]]
    heights = [max(len(v) for v in row) * 34 + 24 for row in prepared]
    page_rows, used = [], 0
    pages = []
    for index in range(1, len(prepared)):
        if page_rows and (used + heights[index] > 1800 or len(page_rows) >= 20):
            pages.append(page_rows)
            page_rows, used = [], 0
        page_rows.append(index)
        used += heights[index]
    pages.append(page_rows)
    for indices in pages:
        top = 20
        height = top + heights[0] + sum(heights[x] for x in indices) + 20
        if height * width > 6_000_000:
            raise ValueError('表格单元内容过长')
        picture = Image.new('RGB', (width, height), '#f4f7f4')
        draw = ImageDraw.Draw(picture)
        y = top
        for rno, idx in enumerate([0, *indices]):
            h = heights[idx]
            draw.rectangle((20, y, width - 20, y + h), fill='#197661' if idx == 0 else ('#ffffff' if rno % 2 else '#e9f0eb'))
            x = 20
            for col, lines in enumerate(prepared[idx]):
                for ln, value in enumerate(lines):
                    draw.text((x + 12, y + 8 + ln * 34), value, font=font, fill='white' if idx == 0 else '#24372e')
                draw.line((x, y, x, y + h), fill='#cbd8cf', width=1)
                x += widths[col]
            draw.line((20, y + h, width - 20, y + h), fill='#cbd8cf', width=1)
            y += h
        output = io.BytesIO()
        picture.save(output, format='PNG', optimize=True)
        yield output.getvalue()


def ensure_schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS generated_tables(token_hash TEXT PRIMARY KEY,expires_at TEXT NOT NULL,created_at TEXT NOT NULL)')


def publish_tables(text, ctx, db, clock=now):
    blocks = list(table_blocks(text))
    if not blocks:
        return text
    config = db.execute("SELECT value FROM config WHERE key='public_base_url'").fetchone()
    base = json.loads(config[0]) if config else ''
    if not base:
        # Local simulation has no public image origin; retain full semantic table.
        return text
    ensure_schema(db)
    folder = ctx.root / 'tables'
    folder.mkdir(exist_ok=True)
    lines = text.splitlines()
    for start, end, title, headers, rows in reversed(blocks):
        links = []
        for page in render_pages(headers, rows):
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode()).hexdigest()
            (folder / (digest + '.png')).write_bytes(page)
            db.execute('INSERT INTO generated_tables VALUES(?,?,?)', (digest, stamp(clock() + timedelta(hours=24)), stamp(clock())))
            links.append(f'![{title.replace("[", "").replace("]", "")}表格]({base}/public/tables/{ctx.id}.{token})')
        lines[start:end] = ['\n\n'.join(links)]
    return '\n'.join(lines)


def public_table(ctx, token, clock=now):
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,60}', token):
        raise PermissionError('表格链接无效')
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect(ctx.db_path) as db:
        ensure_schema(db)
        row = db.execute('SELECT 1 FROM generated_tables WHERE token_hash=? AND expires_at>?', (digest, stamp(clock()))).fetchone()
    path = ctx.root / 'tables' / (digest + '.png')
    if not row or not path.is_file():
        raise PermissionError('表格链接已失效，请重新查询')
    return path


def cleanup(ctx, clock=now):
    with connect(ctx.db_path, True) as db:
        ensure_schema(db)
        old = list(db.execute('SELECT token_hash FROM generated_tables WHERE expires_at<=?', (stamp(clock()),)))
        for row in old:
            (ctx.root / 'tables' / (row[0] + '.png')).unlink(missing_ok=True)
        db.execute('DELETE FROM generated_tables WHERE expires_at<=?', (stamp(clock()),))
