"""Admin-reviewed imports and unique-name registration confirmation."""
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

from .member_sheet import extract, MAX_BYTES, MAX_ROWS


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
        phone TEXT,level INTEGER,group_ids TEXT NOT NULL DEFAULT '[]',
        linked_user_id TEXT,created_by TEXT NOT NULL,created_at TEXT NOT NULL,linked_at TEXT,
        UNIQUE(scope_id,phone))''')
    if 'gender' not in {r[1] for r in db.execute('PRAGMA table_info(pre_registered_members)')}:
        db.execute("ALTER TABLE pre_registered_members ADD COLUMN gender TEXT NOT NULL DEFAULT 'unknown'")
    columns = {r[1]: r for r in db.execute('PRAGMA table_info(pre_registered_members)')}
    if columns['phone'][3]:
        # Nullable phones allow incomplete imports without inventing identities.
        # Copy every historical column and value under a savepoint.
        db.execute('SAVEPOINT nullable_prereg_phone')
        try:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='pre_registered_members'").fetchone()[0]
            db.execute(sql.replace('pre_registered_members', 'prereg_phone_upgrade', 1).replace('phone TEXT NOT NULL', 'phone TEXT'))
            fields = ','.join(columns)
            db.execute(f'INSERT INTO prereg_phone_upgrade({fields}) SELECT {fields} FROM pre_registered_members')
            db.execute('DROP TABLE pre_registered_members')
            db.execute('ALTER TABLE prereg_phone_upgrade RENAME TO pre_registered_members')
            db.execute('RELEASE nullable_prereg_phone')
        except BaseException:
            db.execute('ROLLBACK TO nullable_prereg_phone')
            db.execute('RELEASE nullable_prereg_phone')
            raise
    for column, definition in (('source_key', "TEXT NOT NULL DEFAULT ''"), ('review_required', 'INTEGER NOT NULL DEFAULT 0'), ('identity_attributes', "TEXT NOT NULL DEFAULT '{}'"), ('merged_into_id','INTEGER')):
        if column not in columns:
            db.execute(f'ALTER TABLE pre_registered_members ADD COLUMN {column} {definition}')
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS prereg_source ON pre_registered_members(scope_id,source_key) WHERE source_key<>''")
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS preregistered_account_binding
        ON pre_registered_members(scope_id,linked_user_id) WHERE linked_user_id IS NOT NULL''')
    db.execute('''CREATE TABLE IF NOT EXISTS member_import_previews(
        token_hash TEXT PRIMARY KEY,scope_id TEXT NOT NULL,admin_id TEXT NOT NULL,payload TEXT,
        expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',result TEXT,created_at TEXT NOT NULL)''')


def phone_accounts(db,scope,phone):
    if not phone:return []
    matches=[]
    for row in db.execute("SELECT m.*,COALESCE(p.name,'') AS name FROM member_records m LEFT JOIN profiles p ON p.chat_id=m.chat_id AND p.user_id=m.user_id WHERE m.chat_id=? AND m.phone<>''",(scope,)):
        try:normalized=normalize_phone(row['phone'])
        except ValueError:continue
        if normalized==phone:matches.append(dict(row))
    return matches


def phone_pre_registrations(db,scope,phone):
    if not phone:return []
    matches=[]
    for row in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? AND merged_into_id IS NULL',(scope,)):
        try:normalized=normalize_phone(row['phone'])
        except ValueError:continue
        if normalized==phone:matches.append(dict(row))
    return matches


def registration_match(db,scope,phone,user_id,name=None):
    """Match an administrator-approved name; use phone to disambiguate names.

    With no name argument this remains a phone ownership check for profile edits.
    A roster never replaces an existing account binding.
    """
    ensure_schema(db)
    mismatched_name=False
    if name is not None:
        from .member_matching import normalized
        candidates=[dict(r) for r in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? AND merged_into_id IS NULL',(scope,))
                    if normalized(r['name']) and normalized(r['name'])==normalized(name)]
        if len(candidates)>1:
            candidates=[r for r in candidates if phone and normalize_phone(r['phone'])==phone]
            if len(candidates)>1:raise PermissionError('该手机号存在重复预登记资料，请联系管理员核实')
            if len(candidates)!=1:
                raise ValueError('名册中有重名，请填写登记手机号区分；没有手机号请联系管理员补充')
        if candidates:
            candidate=candidates[0]
            if phone and candidate['phone'] and normalize_phone(candidate['phone'])!=phone:
                raise ValueError('姓名已匹配，手机号与已登记资料不一致，请核对')
            phone=normalize_phone(candidate['phone']) or phone
        else:
            candidate=None
            # A different name cannot claim a phone that belongs to a roster.
            phone_candidates=phone_pre_registrations(db,scope,phone)
            if len(phone_candidates)==1 and not phone_candidates[0]['name']:
                candidate=phone_candidates[0]
            else:
                mismatched_name=bool(phone_candidates)
    owners=phone_accounts(db,scope,phone)
    if len(owners)>1:raise PermissionError('该手机号存在重复账号资料，请联系管理员核实')
    if owners and owners[0]['user_id']!=user_id:
        raise PermissionError('该手机号已关联其他账号，请联系管理员核实，不能重复绑定')
    if mismatched_name:raise ValueError('手机号已在名册中，请核对登记姓名')
    if len(phone_pre_registrations(db,scope,phone))>1:
        raise PermissionError('该手机号存在重复预登记资料，请联系管理员核实')
    if name is not None and candidate and any(r['id']!=candidate['id'] for r in phone_pre_registrations(db,scope,phone)):
        raise PermissionError('该手机号属于另一份预登记资料，请核对本人信息')
    rows=([candidate] if candidate else []) if name is not None else phone_pre_registrations(db,scope,phone)
    if len(rows)>1:raise PermissionError('该手机号存在重复预登记资料，请联系管理员核实')
    if not rows:return None
    row=dict(rows[0])
    if row['linked_user_id'] and row['linked_user_id']!=user_id:
        raise PermissionError('该登记资料已关联其他账号，请联系管理员核实，不能重复绑定')
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
        by_id={r['id']:r for r in rows}
        for row in rows:
            canonical=by_id.get(row.get('merged_into_id'))
            if canonical:
                row['linked_user_id']=canonical['linked_user_id']
                row['phone']=canonical['phone']
            row['status']='linked' if row['linked_user_id'] else 'unbound'
            if status and row['status']!=status:continue
            phone=row.pop('phone')
            row['phone_masked']=masked(phone)
            row['missing_fields']=[label for label,value in (('姓名',row['name']),('手机号',phone),('性别',row['gender']!='unknown'),('级别',row['level'])) if not value]
            row['group_ids']=json.loads(row['group_ids'])
            row['groups']=[{'id':gid,'name':groups.get(gid,'已移除分组')} for gid in row['group_ids']]
            row.pop('scope_id');row.pop('created_by')
            row.pop('identity_attributes', None)
            result.append(row)
        return result

    @staticmethod
    def _parse(content, filename='members.csv'):
        raw_rows, metadata = extract(content, filename)
        result = []
        for raw in raw_rows:
            item = {key: raw[key] for key in ('line','sheet','source_key')}
            item.update(name='', phone='', level=None, gender='unknown', groups=[])
            try:
                extra = raw.get('identity_attributes', {})
                if any(len(v)>160 or any(ord(c)<32 for c in v) or v.startswith(('=','+','@')) for v in extra.values()):
                    raise ValueError('身份属性最多160字，不得包含公式或控制字符')
                item['identity_attributes'] = extra
                if any(str(raw.get(k, '')).startswith('=') for k in ('name','phone','gender','level','groups')):
                    raise ValueError('资料列含公式，请粘贴为数值后导入')
                name = raw.get('name', '').strip()
                if len(name)>80 or any(ord(c)<32 or ord(c)==127 for c in name):
                    raise ValueError('姓名最多80字且不能包含控制字符')
                item['name'] = name
                item['phone'] = normalize_phone(raw.get('phone'))
                if not name and not item['phone']:
                    raise ValueError('姓名与手机号至少填写一项')
                item['gender'] = normalize_gender(raw.get('gender', ''))
                level = unicodedata.normalize('NFKC',raw.get('level','')).strip()
                if level:
                    if not re.fullmatch(r'[1-9](?:级|\.0)?',level):raise ValueError('自评级别仅支持1至9或留空')
                    item['level'] = int(level[0])
                groups = sorted(set(g.strip() for g in re.split(r'[;；|、,，]', raw.get('groups','')) if g.strip()))
                if len(groups)>100 or any(len(g)>60 or any(ord(c)<32 for c in g) for g in groups):
                    raise ValueError('组别名称最多60字')
                item['groups'] = groups
                missing = [label for label, value in (('姓名',name),('手机号',item['phone']),('性别',item['gender']!='unknown'),('级别',item['level'])) if not value]
                item['missing_fields'] = missing
                # Level and groups are optional labels, not registration gates.
                item['review_required'] = not (name and item['phone'] and item['gender'] != 'unknown')
                if missing:item['warning']='待补全：'+'、'.join(missing)
            except ValueError as error:
                item['error']=str(error)
            result.append(item)
        return result, metadata

    def _classify(self,db,items,decisions=None):
        groups={r['id']:r['name'] for r in db.execute('SELECT id,name FROM member_groups WHERE chat_id=?',(self.ctx.scope_id,))}
        seen={};result=[]
        for original in items:
            item=dict(original);item['status']='create'
            if item.get('error'):item['status']='error';result.append(item);continue
            phone=item['phone'];identity=(item['name'],item['level'],item['groups'])
            if not phone:
                existing = db.execute('SELECT id FROM pre_registered_members WHERE scope_id=? AND source_key=?',(self.ctx.scope_id,item.get('source_key',''))).fetchone()
                if existing:item['status']='skip'
                result.append(item);continue
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
                from .member_matching import normalized
                if normalized(account['name']) == normalized(item['name']):
                    item.update(status='skip',warning='已存在同名同手机号会员，保留原档案')
                else:item.update(status='error',error='手机号已存在账号资料，请先在会员管理中核实，不能直接覆盖')
            result.append(item)
        from .member_matching import classify_duplicates
        return classify_duplicates(db,self.ctx.scope_id,result,decisions)

    @staticmethod
    def _visible(rows, metadata, token, expires):
        from .member_matching import masked_value
        errors=[{'line':r['line'],'message':r['error']} for r in rows if r['status']=='error']
        visible=[]
        for row in rows:
            item={k:v for k,v in row.items() if k not in ('phone','identity_attributes','match_signature')}
            item['phone_masked']=masked(row['phone'])
            item['identity_attributes']={k:masked_value(k,v) for k,v in row.get('identity_attributes',{}).items()}
            item['matches']=[{k:v for k,v in candidate.items() if k!='fingerprint'} for candidate in row.get('matches',[])]
            visible.append(item)
        pending=sum(r['status']=='match' for r in rows)
        return {'token':token,'expires_at':expires if token else None,**metadata,'total':len(rows),
                'create_count':sum(r['status']=='create' for r in rows),'skip_count':sum(r['status']=='skip' for r in rows),
                'match_count':pending,'can_commit':not errors and not pending,'errors':errors,'rows':visible}

    def _preview_record(self,db,token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{40,60}',token):
            raise ValueError('导入预览已失效，请重新上传')
        row=db.execute('SELECT * FROM member_import_previews WHERE token_hash=? AND scope_id=? AND admin_id=? AND expires_at>?',
            (hashlib.sha256(token.encode()).hexdigest(),self.ctx.scope_id,self.ctx.actor,stamp(self.clock()))).fetchone()
        if not row:raise ValueError('导入预览已失效或不属于当前管理员，请重新上传')
        return row

    @staticmethod
    def _payload(preview):
        value=json.loads(preview['payload'])
        return {'items':value,'metadata':{},'decisions':{}} if isinstance(value,list) else value

    def resolve(self,token,choices):
        self._admin()
        if not isinstance(choices,list) or not choices or len(choices)>MAX_ROWS:
            raise ValueError('请选择要确认的匹配记录')
        with connect(self.path,True) as db:
            ensure_schema(db)
            preview=self._preview_record(db,token)
            if preview['status']!='pending':raise ValueError('本次导入已完成，请重新上传')
            payload=self._payload(preview);decisions=payload.setdefault('decisions',{})
            seen=set()
            rows={r['source_key']:r for r in self._classify(db,payload['items'],decisions)}
            for choice in choices:
                if not isinstance(choice,dict) or set(choice)!={'source_key','target'}:raise ValueError('匹配选择无效')
                key,target=choice['source_key'],choice['target']
                if not isinstance(key,str) or not isinstance(target,str) or key in seen:raise ValueError('匹配选择无效')
                seen.add(key)
                row=rows.get(key)
                if not row or not row.get('matches'):raise ValueError('匹配候选已变化，请重新预览')
                if target!='new' and not any(c['target']==target for c in row['matches']):raise ValueError('请选择当前提供的候选档案')
                decisions[key]={'target':target,'signature':row['match_signature']}
            rows=self._classify(db,payload['items'],decisions)
            db.execute('UPDATE member_import_previews SET payload=? WHERE token_hash=?',(dumps(payload),preview['token_hash']))
            audit(db,self.ctx.actor,'member_import.match',{'decisions':choices,'scope_id':self.ctx.scope_id})
            return self._visible(rows,payload['metadata'],token,preview['expires_at'])

    def preview(self,content,filename='members.csv'):
        self._admin()
        if not isinstance(filename,str):raise ValueError('文件名无效')
        parsed,metadata=self._parse(content,filename)
        expires=stamp(self.clock()+timedelta(minutes=20));token=None
        with connect(self.path,True) as db:
            ensure_schema(db)
            db.execute('DELETE FROM member_import_previews WHERE expires_at<=?',(stamp(self.clock()),))
            rows=self._classify(db,parsed)
            errors=[{'line':r['line'],'message':r['error']} for r in rows if r['status']=='error']
            if not errors:
                token=secrets.token_urlsafe(32)
                db.execute('INSERT INTO member_import_previews(token_hash,scope_id,admin_id,payload,expires_at,created_at) VALUES(?,?,?,?,?,?)',
                           (hashlib.sha256(token.encode()).hexdigest(),self.ctx.scope_id,self.ctx.actor,dumps({'items':parsed,'metadata':metadata,'decisions':{}}),expires,stamp(self.clock())))
        return self._visible(rows,metadata,token,expires)

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
            payload=self._payload(preview)
            items=self._classify(db,payload['items'],payload['decisions'])
            errors=[r for r in items if r['status']=='error']
            if errors:raise ValueError('预览后资料发生冲突，本次未导入任何记录，请重新预览：第'+str(errors[0]['line'])+'行 '+errors[0]['error'])
            if any(r['status']=='match' for r in items):
                raise ValueError('存在待确认的重复人员，本次未导入任何记录，请核对匹配；资料变化时需重新预览')
            count=0
            for row in items:
                if row['status']=='skip':continue
                ids=[]
                for name in row['groups']:
                    found=db.execute('SELECT id FROM member_groups WHERE chat_id=? AND name=?',(self.ctx.scope_id,name)).fetchone()
                    ids.append(found[0] if found else db.execute('INSERT INTO member_groups(chat_id,name,created_at) VALUES(?,?,?)',(self.ctx.scope_id,name,stamp(self.clock()))).lastrowid)
                db.execute('INSERT INTO pre_registered_members(scope_id,name,phone,level,gender,group_ids,created_by,created_at,source_key,review_required,identity_attributes) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (self.ctx.scope_id,row['name'],row['phone'] or None,row['level'],row.get('gender','unknown'),dumps(sorted(ids)),self.ctx.actor,stamp(self.clock()),row.get('source_key',''),int(row.get('review_required',False)),dumps(row.get('identity_attributes',{}))))
                count+=1
            result={'imported':count,'skipped':len(items)-count,'total':len(items),'already_committed':False}
            db.execute("UPDATE member_import_previews SET status='committed',payload=NULL,result=? WHERE token_hash=?",(dumps(result),preview['token_hash']))
            audit(db,self.ctx.actor,'member_import.commit',{'imported':count,'skipped':len(items)-count})
            return result

    def create(self, data):
        self._admin()
        allowed={'name','phone','gender','requested_level','level','groups'}
        if not isinstance(data,dict) or set(data)-allowed:raise ValueError('预登记字段无效')
        stream=io.StringIO(newline='')
        writer=csv.writer(stream)
        writer.writerow(['姓名','手机号','性别','级别','组别'])
        writer.writerow([data.get('name',''),data.get('phone',''),data.get('gender',''),data.get('level',data.get('requested_level')) or '',data.get('groups','')])
        preview=self.preview(stream.getvalue().encode())
        if not preview['can_commit']:raise ValueError(preview['errors'][0]['message'] if preview['errors'] else '存在同名或重复人员，请在批量登记中核对匹配')
        return self.commit(preview['token'])

    def update(self, record_id, data):
        self._admin()
        if not isinstance(data,dict) or set(data)-{'phone','name','gender','level'}:raise ValueError('预登记字段无效')
        with connect(self.path,True) as db:
            ensure_schema(db)
            row=db.execute('SELECT * FROM pre_registered_members WHERE scope_id=? AND id=?',(self.ctx.scope_id,record_id)).fetchone()
            if not row:raise ValueError('预登记不存在')
            if row['linked_user_id'] or row['merged_into_id']:raise ValueError('已关联资料请在会员档案中修改')
            value=dict(row)
            for key in data:
                if key=='phone':
                    phone=normalize_phone(data[key],True)
                    others=[r for r in phone_pre_registrations(db,self.ctx.scope_id,phone) if r['id']!=record_id]
                    value[key]=phone
                elif key=='name':
                    from .members import display_name
                    value[key]=display_name(data[key], '')
                elif key=='gender':value[key]=normalize_gender(data[key])
                elif key=='level':
                    from .members import Members
                    value[key]=Members._level(data[key]) if data[key] is not None else None
            from .registration_reconciliation import existing_account, link_existing
            account, reason = existing_account(db, self.ctx.scope_id, value)
            if phone_accounts(db,self.ctx.scope_id,value['phone']) and not account:
                raise ValueError(reason)
            others=[r for r in phone_pre_registrations(db,self.ctx.scope_id,value['phone']) if r['id']!=record_id]
            if others and not (account and account.get('_canonical_id')):
                raise ValueError('手机号已属于另一份名册，请核对重复档案')
            required = not (value['name'] and value['phone'] and value['gender'] != 'unknown')
            if not others:
                db.execute('UPDATE pre_registered_members SET name=?,phone=?,gender=?,level=?,review_required=? WHERE id=? AND scope_id=?',
                           (value['name'],value['phone'],value['gender'],value['level'],int(required),record_id,self.ctx.scope_id))
            linked, _ = link_existing(db,self.ctx,value,self.clock())
            audit(db,self.ctx.actor,'member_import.update',{'id':record_id,'fields':sorted(data)})
        return next(r for r in self.list() if r['id']==record_id) | {
            'message': '已匹配现有会员并关联' if linked else '资料已保存，待本人在群里发送“注册确认”'}

    def reconcile(self, record_ids=None, *, commit=False):
        """Preview or link unambiguous names to approved accounts, refusing phone conflicts."""
        self._admin()
        if record_ids is not None and (not isinstance(record_ids,list) or len(record_ids)>1000
                or any(type(i) is not int or i<1 for i in record_ids)):
            raise ValueError('请选择有效的待关联人员')
        with connect(self.path,True) as db:
            ensure_schema(db)
            from .registration_reconciliation import existing_account, link_existing
            rows=[dict(r) for r in db.execute(
                'SELECT * FROM pre_registered_members WHERE scope_id=? AND linked_user_id IS NULL AND merged_into_id IS NULL',
                (self.ctx.scope_id,))]
            if record_ids is not None:rows=[r for r in rows if r['id'] in record_ids]
            result=[];linked=0
            for row in rows:
                account, reason=existing_account(db,self.ctx.scope_id,row)
                if commit and account:
                    applied,_=link_existing(db,self.ctx,row,self.clock())
                    linked+=int(applied)
                result.append({'id':row['id'],'name':row['name'],'phone_masked':masked(row['phone']),
                               'can_link':bool(account),'reason':reason or account['_basis']})
        return {'items':result,'matched':sum(r['can_link'] for r in result),'linked':linked,
                'waiting':sum(not r['can_link'] for r in result)}
