"""Atomic request receipts for browser retries (including additive guests)."""
import hashlib
import re
from .db import dumps, stamp


class ReplayedEnrollment(Exception):
    pass


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS enrollment_web_receipts(
        scope_id TEXT NOT NULL,user_id TEXT NOT NULL,request_id TEXT NOT NULL,
        signature TEXT NOT NULL,created_at TEXT NOT NULL,
        PRIMARY KEY(scope_id,user_id,request_id))''')


def begin(db, ctx, request_id, payload):
    if request_id is None:
        return None
    if not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id):
        raise ValueError('请刷新报名页面后重试')
    schema(db)
    signature=hashlib.sha256(dumps(payload).encode()).hexdigest()
    existing=db.execute('SELECT signature FROM enrollment_web_receipts WHERE scope_id=? AND user_id=? AND request_id=?',
                        (ctx.scope_id,ctx.actor,request_id)).fetchone()
    if existing:
        if existing['signature'] != signature:
            raise ValueError('此操作已经提交，请重新选择')
        raise ReplayedEnrollment()
    return signature


def finish(db, ctx, request_id, signature, at):
    if signature:
        db.execute('INSERT INTO enrollment_web_receipts VALUES(?,?,?,?,?)',
                   (ctx.scope_id,ctx.actor,request_id,signature,stamp(at)))
