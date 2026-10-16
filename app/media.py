"""Bounded uploads, validated types and revocable expiring public links."""
from datetime import timedelta
from pathlib import Path, PurePosixPath
import io
import re
import secrets
import uuid
import zipfile
from PIL import Image, UnidentifiedImageError
from .db import connect, stamp, now, audit
from .security import digest
from .service import Service
from .messages import escape, safe_markdown

MAX_BYTES = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 20_000_000


def validate_file(filename, content):
    name = re.split(r'[/\\]', filename or '附件')[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', '_', name).strip(' .')[:120] or '附件'
    suffix = Path(name).suffix.lower()
    if not content or len(content) > MAX_BYTES:
        raise ValueError('附件必须为1字节至10MiB')
    image_types = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP'}
    if suffix in image_types:
        try:
            with Image.open(io.BytesIO(content)) as picture:
                if picture.format != image_types[suffix] or picture.width * picture.height > 20_000_000:
                    raise ValueError('图片类型或像素尺寸不符合限制')
                picture.load()
                # Re-encode to discard trailing HTML, metadata and active polyglot data.
                picture = picture.convert('RGB' if picture.format == 'JPEG' else 'RGBA')
                output = io.BytesIO()
                picture.save(output, format=image_types[suffix])
                content = output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ValueError('图片损坏或实际类型不符') from None
        mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[image_types[suffix]]
        kind = 'image'
    elif suffix == '.pdf' and content.startswith(b'%PDF-') and b'%%EOF' in content[-2048:]:
        mime, kind = 'application/pdf', 'file'
    elif suffix in ('.txt', '.csv'):
        try:
            text = content.decode('utf-8-sig')
        except UnicodeError:
            raise ValueError('文本附件须为UTF-8编码') from None
        if '\x00' in text or re.search(r'<\s*(?:html|script|svg|iframe|!doctype)\b', text, re.I):
            raise ValueError('不接受HTML、SVG或脚本附件')
        mime, kind = 'text/plain; charset=utf-8', 'file'
    elif suffix in ('.docx', '.xlsx', '.pptx'):
        expected = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml', '.pptx': 'ppt/presentation.xml'}[suffix]
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                files = archive.infolist()
                if len(files) > 1000 or sum(f.file_size for f in files) > 50 * 1024 * 1024 or expected not in archive.namelist() or '[Content_Types].xml' not in archive.namelist():
                    raise ValueError('Office附件结构或大小不符合限制')
                for file in files:
                    path = PurePosixPath(file.filename)
                    if path.is_absolute() or '..' in path.parts or 'vbaproject' in file.filename.lower() or file.flag_bits & 1:
                        raise ValueError('不接受宏或加密Office附件')
        except (zipfile.BadZipFile, OSError):
            raise ValueError('Office附件类型不符') from None
        mime, kind = 'application/octet-stream', 'file'
    else:
        raise ValueError('支持PNG/JPEG/WebP、PDF、UTF-8文本/CSV及无宏Office附件')
    if len(content) > MAX_BYTES:
        raise ValueError('处理后的图片超过10MiB')
    return name, content, mime, kind


class Media:
    def __init__(self, ctx, clock=now):
        self.ctx, self.clock = ctx, clock
        self.service = Service(ctx, clock)

    def _manage(self, tournament_id=None):
        if tournament_id is not None:
            from .tournaments import Tournaments
            Tournaments(self.ctx, self.clock).get(tournament_id)
            if self.ctx.can('event.manage'):
                return
        self.service.require_admin('message.manage')

    def list(self, tournament_id=None):
        self._manage(tournament_id)
        with connect(self.ctx.db_path) as db:
            rows = db.execute('SELECT * FROM media WHERE (? IS NULL OR tournament_id=?) ORDER BY created_at DESC', (tournament_id, tournament_id))
            return [self.public_record(dict(row)) for row in rows]

    @staticmethod
    def public_record(row):
        return {k: v for k, v in row.items() if k != 'path'} | {'url': f"/api/media/{row['id']}/download"}

    def get(self, media_id):
        with connect(self.ctx.db_path) as db:
            row = db.execute('SELECT * FROM media WHERE id=?', (media_id,)).fetchone()
        if not row:
            raise ValueError('附件不存在')
        return dict(row)

    def save(self, filename, content, tournament_id=None, caption=''):
        self._manage(tournament_id)
        if len(caption) > 500:
            raise ValueError('照片说明最多500字符')
        name, content, mime, kind = validate_file(filename, content)
        media_id = uuid.uuid4().hex
        folder = self.ctx.root / 'uploads'
        folder.mkdir(exist_ok=True)
        path = folder / (media_id + '.bin')
        path.write_bytes(content)
        try:
            with connect(self.ctx.db_path, True) as db:
                db.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?,?)',
                           (media_id, name, path.name, mime, kind, len(content), tournament_id, caption, stamp(self.clock())))
                audit(db, self.ctx.actor, 'media.upload', {'media_id': media_id, 'tournament_id': tournament_id, 'size': len(content)})
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return self.public_record(self.get(media_id))

    def download_path(self, row):
        root = (self.ctx.root / 'uploads').resolve()
        path = (root / row['path']).resolve()
        if path.parent != root or not path.is_file():
            raise ValueError('附件文件不可用')
        return path

    def link(self, media_id, hours=24):
        row = self.get(media_id)
        self._manage(row['tournament_id'])
        base = self.service.config()['public_base_url']
        if not base:
            raise ValueError('请先设置公开服务地址以发送附件链接')
        token = secrets.token_urlsafe(32)
        expires = stamp(self.clock() + timedelta(hours=hours))
        with connect(self.ctx.db_path, True) as db:
            db.execute('INSERT INTO media_links(token_hash,media_id,expires_at) VALUES(?,?,?)', (digest(token), media_id, expires))
        return {'url': f'{base}/public/media/{self.ctx.id}.{token}', 'expires_at': expires}

    def public_download(self, token):
        with connect(self.ctx.db_path) as db:
            row = db.execute('SELECT media_id FROM media_links WHERE token_hash=? AND revoked=0 AND expires_at>?', (digest(token), stamp(self.clock()))).fetchone()
        if not row:
            raise PermissionError('下载链接已失效或被撤销')
        return self.get(row['media_id'])

    def revoke(self, media_id):
        row = self.get(media_id)
        self._manage(row['tournament_id'])
        with connect(self.ctx.db_path, True) as db:
            count = db.execute('UPDATE media_links SET revoked=1 WHERE media_id=?', (media_id,)).rowcount
            audit(db, self.ctx.actor, 'media.revoke', {'media_id': media_id})
        return {'revoked': count}

    def compose(self, title, text, media_ids, publish=False):
        self.service.require_admin('message.manage')
        if len(title) > 200 or len(text) > 20000 or not isinstance(media_ids, list) or len(media_ids) > 10:
            raise ValueError('标题、正文或附件数量超出限制')
        if not title.strip() and not text.strip() and not media_ids:
            raise ValueError('消息不能为空')
        records = [self.get(media_id) for media_id in media_ids]
        for record in records:
            self._manage(record['tournament_id'])
        lines = ([f'# {escape(title)}'] if title else []) + ([safe_markdown(text)] if text else [])
        for row in records:
            url = self.link(row['id'])['url'] if publish else f"/api/media/{row['id']}/download"
            lines.append(('!' if row['kind'] == 'image' else '') + f"[{escape(row['name'])}]({url})")
        if publish and records:
            lines.append('附件链接24小时内有效，可由管理员提前撤销。')
        return '\n\n'.join(lines)
