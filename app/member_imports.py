"""Admin-reviewed CSV pre-registration; account binding uses only exact phones."""
import csv
from datetime import timedelta
import hashlib
import io
import json
import re
import secrets
import unicodedata

from .db import connect, dumps, audit, stamp, now
from .gender import normalize_gender

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
HEADERS = {'姓名':'name','name':'name','手机号':'phone','手机号码':'phone','手机':'phone','phone':'phone',
           '性别':'gender','gender':'gender','自评级别':'level','自评等级':'level','等级':'level','级别':'level','level':'level','组别':'groups','分组':'groups','groups':'groups'}


def normalize_phone(value, required=False):
    text=unicodedata.normalize('NFKC',str(value or '')).strip()
    text=re.sub(r'[\s()\-]','',text)
    if not text:
        if required:raise ValueError('手机号必填')
        return ''
    if text.startswith('0086'):text=text[4:]
    elif text.startswith('+86'):text=text[3:]
    elif text.startswith('86') and len(text)==13:text=text[2:]
    elif text.startswith('00'):text='+'+text[2:]
    if not re.fullmatch(r'(?:1[3-9]\d{9}|\+[1-9]\d{7,14})',text,re.ASCII):
        raise ValueError('手机号须为11位中国手机号或带国家区号的国际号码')
    return text


def masked(phone):
    return phone[:3]+'****'+phone[-4:] if phone else ''


def ensure_schema(db):
    # execute(), not executescript(): this may run inside Members.apply's transaction.
    db.execute('''CREATE TABLE IF NOT EXISTS pre_registered_members(
        id INTEGER PRIMARY KEY AUTOINCREMENT,scope_id TEXT NOT NULL,name TEXT NOT NULL,
        phone TEXT NOT NULL,level INTEGER,group_ids TEXT NOT NULL DEFAULT '[]',
        linked_user_id TEXT,created_by TEXT NOT NULL,created_at TEXT NOT NULL,linked_at TEXT,
        UNIQUE(scope_id,phone))''')
    if 'gender' not in {r[1] for r in db.execute('PRAGMA table_info(pre_registered_members)')}:
        db.execute("ALTER TABLE pre_registered_members ADD COLUMN gender TEXT NOT NULL DEFAULT 'unknown'")
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS preregistered_account_binding
        ON pre_registered_members(scope_id,linked_user_id) WHERE linked_user_id IS NOT NULL''')
    db.execute('''CREATE TABLE IF NOT EXISTS member_import_previews(
        token_hash TEXT PRIMARY KEY,scope_id TEXT NOT NULL,admin_id TEXT NOT NULL,payload TEXT,
        expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',result TEXT,created_at TEXT NOT NULL)''')


def phone_accounts(db,scope,phone):
    matches=[]
    for row in db.execute("SELECT m.*,COALESCE(p.name,'') AS name FROM member_records m LEFT JOIN profiles p ON p.chat_id=m.chat_id AND p.user_id=m.user_id WHERE m.chat_id=? AND m.phone<>''",(scope,)):
        try:normalized=normalize_phone(row['phone'])
        except ValueError:continue
        if normalized==phone:matches.append(dict(row))
    return matches


def phone_pre_registrations(db,scope,phone):
    matches=[]
    for row in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=?',(scope,)):
        try:normalized=normalize_phone(row['phone'])
        except ValueError:continue
        if normalized==phone:matches.append(dict(row))
    return matches


def registration_match(db,scope,phone,user_id):
    """Return an unbound, approved pre-registration, never identify by name."""
    ensure_schema(db)
    owners=phone_accounts(db,scope,phone)
    if len(owners)>1:raise PermissionError('该手机号存在重复账号资料，请联系管理员核实')
    if owners and owners[0]['user_id']!=user_id:
        raise PermissionError('该手机号已关联其他账号，请联系管理员核实，不能重复绑定')
    rows=phone_pre_registrations(db,scope,phone)
    if len(rows)>1:raise PermissionError('该手机号存在重复预登记资料，请联系管理员核实')
    if not rows:return None
    row=dict(rows[0])
    if row['linked_user_id'] and row['linked_user_id']!=user_id:
        raise PermissionError('该手机号已关联其他账号，请联系管理员核实，不能重复绑定')
    prior=db.execute('SELECT id FROM pre_registered_members WHERE scope_id=? AND linked_user_id=?',(scope,user_id)).fetchone()
    if prior and prior['id']!=row['id']:
        raise PermissionError('当前账号已关联预登记档案，不能换绑其他档案')
    # A previously linked account later rejected by an administrator must submit
    # a fresh application; the old import must not silently re-approve it.
    return row if row['linked_user_id'] is None else None


class MemberImports:
    def __init__(self,ctx,clock=now):
        self.ctx,self.clock,self.path=ctx,clock,ctx.db_path

    def _admin(self):
        self.ctx.require('member.manage')

    def template(self):
        self._admin()
        return '姓名,手机号,性别,自评级别,组别\r\n'.encode('utf-8-sig')

    def list(self,status=None):
        self._admin()
        if status not in (None,'unbound','linked'):raise ValueError('预登记状态无效')
        with connect(self.path) as db:
            ensure_schema(db)
            groups={r['id']:r['name'] for r in db.execute('SELECT id,name FROM member_groups WHERE chat_id=?',(self.ctx.scope_id,))}
            rows=[dict(r) for r in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? ORDER BY id DESC',(self.ctx.scope_id,))]
        result=[]
        for row in rows:
            row['status']='linked' if row['linked_user_id'] else 'unbound'
            if status and row['status']!=status:continue
            row['phone_masked']=masked(row.pop('phone'))
            row['group_ids']=json.loads(row['group_ids'])
            row['groups']=[{'id':gid,'name':groups.get(gid,'已移除分组')} for gid in row['group_ids']]
            row.pop('scope_id');row.pop('created_by')
            result.append(row)
        return result

    @staticmethod
    def _parse(content):
        if not isinstance(content,bytes) or not content or len(content)>MAX_BYTES:
            raise ValueError('CSV文件须为1字节至2MiB')
        try:text=content.decode('utf-8-sig');encoding='UTF-8'
        except UnicodeDecodeError:
            try:text=content.decode('gb18030');encoding='GB18030'
            except UnicodeDecodeError:raise ValueError('CSV须为UTF-8、UTF-8 BOM或GB18030编码') from None
        if '\x00' in text:raise ValueError('CSV内容无效')
        reader=csv.reader(io.StringIO(text,newline=''),strict=True)
        try:header=next(reader)
        except (StopIteration,csv.Error):raise ValueError('CSV缺少表头') from None
        names=[HEADERS.get(value.strip().lower()) for value in header]
        if None in names or len(set(names))!=len(names) or not {'name','phone'}<=set(names):
            raise ValueError('CSV仅支持姓名、手机号、性别、自评级别、组别，姓名与手机号列必填；不得导入账号或管理员角色')
        result=[]
        try:
            for values in reader:
                if not any(v.strip() for v in values):continue
                if len(result)>=MAX_ROWS:raise ValueError('CSV最多1000条记录')
                item={'line':reader.line_num,'name':'','phone':'','level':None,'gender':'unknown','groups':[]}
                try:
                    if len(values)!=len(names):raise ValueError('列数与表头不一致')
                    raw=dict(zip(names,values));name=raw['name'].strip()
                    if not name or len(name)>80 or any(ord(c)<32 or ord(c)==127 for c in name):raise ValueError('姓名必填，最多80字且不能包含控制字符')
                    item['name']=name;item['phone']=normalize_phone(raw['phone'],True)
                    item['gender']=normalize_gender(raw.get('gender', '').strip())
                    if item['gender']=='unknown':item['warning']='性别未填写，参加男双、女双或混双前需补齐'
                    level=raw.get('level','').strip()
                    if level:
                        if not re.fullmatch(r'[1-9](?:级)?',level):raise ValueError('自评级别仅支持1至9或留空')
                        item['level']=int(level[0])
                    groups=sorted(set(g.strip() for g in re.split(r'[;；|、]',raw.get('groups','')) if g.strip()))
                    if len(groups)>100 or any(len(g)>60 or any(ord(c)<32 for c in g) for g in groups):raise ValueError('组别名称最多60字，多个组别用分号分隔')
                    item['groups']=groups
                except ValueError as error:item['error']=str(error)
                result.append(item)
        except csv.Error:raise ValueError('CSV引号或行结构无效') from None
        if not result:raise ValueError('CSV没有会员记录')
        return result,encoding

    def _classify(self,db,items):
        groups={r['id']:r['name'] for r in db.execute('SELECT id,name FROM member_groups WHERE chat_id=?',(self.ctx.scope_id,))}
        seen={};result=[]
        for original in items:
            item=dict(original);item['status']='create'
            if item.get('error'):item['status']='error';result.append(item);continue
            phone=item['phone'];identity=(item['name'],item['level'],item['groups'])
            if phone in seen:
                previous=result[seen[phone]]
                if (previous['name'],previous['level'],previous['groups'])!=identity or previous.get('gender', 'unknown') != item.get('gender', 'unknown') or previous['status']=='error':
                    item.update(status='error',error='同一手机号存在不同资料，本次导入全部取消')
                    previous.update(status='error',error='同一手机号存在不同资料，本次导入全部取消')
                else:item['status']='skip'
                result.append(item);continue
            seen[phone]=len(result)
            existing=phone_pre_registrations(db,self.ctx.scope_id,phone)
            accounts=phone_accounts(db,self.ctx.scope_id,phone)
            if len(existing)>1 or len(accounts)>1:
                item.update(status='error',error='手机号对应重复档案，请先由管理员核实')
            elif existing:
                row=existing[0];group_names=sorted(groups.get(gid,'#missing:'+str(gid)) for gid in json.loads(row['group_ids']))
                if accounts and row['linked_user_id']!=accounts[0]['user_id']:
                    item.update(status='error',error='预登记手机号已存在未关联的账号资料，请先由管理员核实')
                elif (row['name'],row['level'],group_names)==identity and (item.get('gender', 'unknown') == 'unknown' or row.get('gender', 'unknown') == item['gender']):item['status']='skip'
                else:item.update(status='error',error='手机号已存在且资料不同，禁止覆盖或换绑')
            elif accounts:
                account=accounts[0]
                account_groups=sorted(r[0] for r in db.execute('SELECT g.name FROM member_groups g JOIN member_group_links l ON l.group_id=g.id AND l.chat_id=g.chat_id WHERE l.chat_id=? AND l.user_id=?',(self.ctx.scope_id,account['user_id'])))
                if account['status']=='approved' and (account['name'],account['level'],account_groups)==identity and (item.get('gender', 'unknown') == 'unknown' or account.get('gender', 'unknown') == item['gender']):item['status']='skip'
                else:item.update(status='error',error='手机号已存在账号资料，请先在会员管理中核实，不能直接覆盖')
            result.append(item)
        return result

    def preview(self,content,filename='members.csv'):
        self._admin()
        if not isinstance(filename,str) or not filename.lower().endswith('.csv'):raise ValueError('请上传.csv文件')
        parsed,encoding=self._parse(content)
        expires=stamp(self.clock()+timedelta(minutes=20));token=None
        with connect(self.path,True) as db:
            ensure_schema(db)
            db.execute('DELETE FROM member_import_previews WHERE expires_at<=?',(stamp(self.clock()),))
            rows=self._classify(db,parsed)
            errors=[{'line':r['line'],'message':r['error']} for r in rows if r['status']=='error']
            if not errors:
                token=secrets.token_urlsafe(32)
                db.execute('INSERT INTO member_import_previews(token_hash,scope_id,admin_id,payload,expires_at,created_at) VALUES(?,?,?,?,?,?)',
                           (hashlib.sha256(token.encode()).hexdigest(),self.ctx.scope_id,self.ctx.actor,dumps(parsed),expires,stamp(self.clock())))
        visible=[{k:v for k,v in r.items() if k!='phone'}|{'phone_masked':masked(r['phone'])} for r in rows]
        return {'token':token,'expires_at':expires if token else None,'encoding':encoding,'total':len(rows),'create_count':sum(r['status']=='create' for r in rows),
                'skip_count':sum(r['status']=='skip' for r in rows),'can_commit':not errors,'errors':errors,'rows':visible}

    def commit(self,token):
        self._admin()
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,60}',token):raise ValueError('导入预览已失效，请重新上传')
        with connect(self.path,True) as db:
            ensure_schema(db)
            preview=db.execute('SELECT * FROM member_import_previews WHERE token_hash=? AND scope_id=? AND admin_id=? AND expires_at>?',
                               (hashlib.sha256(token.encode()).hexdigest(),self.ctx.scope_id,self.ctx.actor,stamp(self.clock()))).fetchone()
            if not preview:raise ValueError('导入预览已失效或不属于当前管理员，请重新上传')
            if preview['status']=='committed':return json.loads(preview['result'])|{'already_committed':True}
            if preview['status']!='pending':raise ValueError('导入预览已失效，请重新上传')
            items=self._classify(db,json.loads(preview['payload']))
            errors=[r for r in items if r['status']=='error']
            if errors:raise ValueError('预览后资料发生冲突，本次未导入任何记录，请重新预览：第'+str(errors[0]['line'])+'行 '+errors[0]['error'])
            count=0
            for row in items:
                if row['status']=='skip':continue
                ids=[]
                for name in row['groups']:
                    found=db.execute('SELECT id FROM member_groups WHERE chat_id=? AND name=?',(self.ctx.scope_id,name)).fetchone()
                    ids.append(found[0] if found else db.execute('INSERT INTO member_groups(chat_id,name,created_at) VALUES(?,?,?)',(self.ctx.scope_id,name,stamp(self.clock()))).lastrowid)
                db.execute('INSERT INTO pre_registered_members(scope_id,name,phone,level,gender,group_ids,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
                           (self.ctx.scope_id,row['name'],row['phone'],row['level'],row.get('gender','unknown'),dumps(sorted(ids)),self.ctx.actor,stamp(self.clock())))
                count+=1
            result={'imported':count,'skipped':len(items)-count,'total':len(items),'already_committed':False}
            db.execute("UPDATE member_import_previews SET status='committed',payload=NULL,result=? WHERE token_hash=?",(dumps(result),preview['token_hash']))
            audit(db,self.ctx.actor,'member_import.commit',{'imported':count,'skipped':len(items)-count})
            return result
