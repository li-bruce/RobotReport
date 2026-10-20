"""Admin-confirmed roster duplicate candidates; never account binding or merging."""
import hashlib
import json
import re
import unicodedata

IDENTITY_COLUMNS = {
    'email': ('邮箱', '电子邮箱', '电子邮件', 'email', 'emailaddress'),
    'student_number': ('学号', '学生编号', 'studentid', 'studentnumber'),
    'employee_number': ('工号', '员工编号', '职工编号', 'employeeid', 'employeenumber'),
    'member_number': ('会员号', '会员编号', '会员卡号', 'membernumber', 'membershipnumber'),
    'wechat': ('微信号', '微信账号', 'wechat', 'wechatid'),
    'document_number': ('证件号', '证件号码', '身份证号', '身份证号码', '护照号', 'passportnumber'),
    'nickname': ('昵称', '网名', 'nickname'),
}
LABELS = {'name':'姓名', 'phone':'手机号', **{key: aliases[0] for key, aliases in IDENTITY_COLUMNS.items()}}


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def attributes(row):
    extra = row.get('identity_attributes') or {}
    if isinstance(extra, str):
        extra = json.loads(extra)
    return {'name':row.get('name',''), 'phone':row.get('phone',''), **extra}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def masked_value(key, value):
    value = str(value)
    if key in ('name','nickname'):
        return value
    if len(value) <= 4:
        return '••••'
    return value[:2] + '••••' + value[-2:]


def directory(db, scope):
    result = {}
    prereg = [dict(r) for r in db.execute('SELECT * FROM pre_registered_members WHERE scope_id=?',(scope,))]
    linked = {r['linked_user_id']:r for r in prereg if r['linked_user_id']}
    for row in db.execute('''SELECT m.*,p.name FROM member_records m JOIN profiles p
            ON p.chat_id=m.chat_id AND p.user_id=m.user_id WHERE m.chat_id=?''',(scope,)):
        row = dict(row)
        row['identity_attributes'] = linked.get(row['user_id'],{}).get('identity_attributes','{}')
        if row['name'] == row['user_id']:
            row['name'] = ''
        row.update(target='member:'+row['user_id'],source='会员档案')
        result[row['target']] = row
    for row in prereg:
        if row['linked_user_id'] in linked and 'member:'+row['linked_user_id'] in result:
            continue
        row.update(target='prereg:'+str(row['id']),source='预登记档案')
        result[row['target']] = row
    return result


def candidates(item, entries):
    own = attributes(item)
    matches = []
    for target, row in entries.items():
        other = attributes(row)
        evidence = [{'field':key,'label':LABELS[key],'value':masked_value(key,value)}
                    for key,value in own.items() if key in LABELS and normalized(value)
                    and normalized(value)==normalized(other.get(key))]
        if not evidence:
            continue
        matches.append({'target':target,'name':row.get('name') or '待补姓名','source':row['source'],
            'gender':row.get('gender','unknown'),'level':row.get('level'),
            'phone_masked':masked_value('phone',row['phone']) if row.get('phone') else '',
            'evidence':evidence,'fingerprint':fingerprint({'target':target,'attributes':other,
                'gender':row.get('gender'),'level':row.get('level'),'status':row.get('status'),
                'linked_user_id':row.get('linked_user_id')})})
    return sorted(matches,key=lambda r:(-len(r['evidence']),r['name'],r['target']))


def classify_duplicates(db, scope, rows, decisions=None):
    entries=directory(db,scope)
    decisions=decisions or {}
    for row in rows:
        key=row['source_key']
        if row['status'] != 'create':
            continue
        matches=candidates(row,entries)
        row['matches']=matches
        signature=fingerprint([(r['target'],r['fingerprint']) for r in matches])
        row['match_signature']=signature
        choice=decisions.get(key)
        if choice and choice.get('signature') == signature:
            target=choice.get('target')
            if target=='new':
                row['match_decision']='new'
            elif any(c['target']==target for c in matches):
                row.update(status='skip',match_decision=target,warning='已确认匹配，保留原档案')
        elif choice or matches:
            row['status']='match'
            if choice:
                row['warning']='候选资料已变化，请重新预览'
        if row['status']=='create':
            # Later rows can be reviewed against a genuinely new row of this batch.
            entries['row:'+key]={**row,'target':'row:'+key,'source':f"本次文件 · {row['sheet']} 第{row['line']}行"}
    return rows
