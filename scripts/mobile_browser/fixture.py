"""Disposable mobile-browser fixture, exclusively local synthetic data."""
import os,sys,json,socket,ipaddress
from pathlib import Path
from datetime import datetime
from dataclasses import replace

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR']).resolve()
project=Path(__file__).resolve().parents[2]
run_id=os.environ['ROBORT_MOBILE_RUN_ID']
port=int(os.environ['ROBORT_MOBILE_PORT'])
origin=f'http://127.0.0.1:{port}'
sys.path.insert(0,str(project))
sys.dont_write_bytecode=True
state=base/'state'
os.environ.update(ROBORT_STATE_DIR=str(state),ROBORT_LIVE_SEND='0')
# Deny DNS and connections outside loopback, including during app setup.
def allow_host(host):
    if host is None:return True
    try:return ipaddress.ip_address(host).is_loopback
    except ValueError:return host=='localhost'
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
original_getaddrinfo=socket.getaddrinfo
def local_connect(sock,address):
    if isinstance(address,tuple) and not allow_host(address[0]):
        raise OSError('External network disabled for mobile fixture')
    return original_connect(sock,address)
def local_connect_ex(sock,address):
    if isinstance(address,tuple) and not allow_host(address[0]):
        raise OSError('External network disabled for mobile fixture')
    return original_connect_ex(sock,address)
def local_getaddrinfo(host,*args,**kwargs):
    if not allow_host(host):raise OSError('External DNS disabled for mobile fixture')
    return original_getaddrinfo(host,*args,**kwargs)
socket.socket.connect=local_connect
socket.socket.connect_ex=local_connect_ex
socket.getaddrinfo=local_getaddrinfo

from app.main import create_app
from app.security import Principal
from app.service import Service
from app.members import Members
from app.people import People
from app.tournaments import Tournaments
from app.event_management import EventManagement
from app.registration import create_form
from app.db import TZ

clock=lambda:datetime(2026,10,14,16,0,tzinfo=TZ)
import app.competitions as competition_module
competition_module._now=clock
app=create_app(state,start_scheduler=False,live=False,clock=clock)
# Exercise the actual onboarding state machine against a local mock provider.
# All sockets remain loopback-only and ordinary business sending stays disabled.
import httpx
import app.bot_connection as connection_module
connection_send=connection_module.send_test
async def fixture_connection_send(*args,**kwargs):
    kwargs['live']=True
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'result':'ok'}))) as client:
        kwargs['client']=client
        return await connection_send(*args,**kwargs)
connection_module.send_test=fixture_connection_send
password='local-browser-fixture-'+run_id
app.state.passwords.set_initial(password)
p=app.state.platform
principal=Principal('local-mobile-admin','platform_admin',())
admin=p.workspace_context()
Service(admin,clock).update_config({'public_base_url':origin})
from app.bot_capabilities import ALL_CAPABILITIES
bots=[p.create(principal,{'name':name,'capabilities':list(ALL_CAPABILITIES),'chat_id':'mobile-fixture-group','robot_key':'synthetic-only-'+str(i)}) for i,name in enumerate(('每日羽毛球活动与报名助手','大型赛事组织与比分记录助手'))]
ctx=p.context(principal,bots[0]['id'])
group=People(admin,clock).save_group('周三晚间提高组与混双练习小队')
users=[]
for i in range(12):
    user='mobile-fixture-'+str(i)
    actor=replace(ctx,actor=user,source='callback',is_admin=False)
    name=('张三','李四','王小明','陈羽','赵晨','刘阳','周宁','林雨','吴可','孙悦','长姓名演示会员周三羽球活动报名','待审核会员林雨')[i]
    Members(actor,clock).apply(name,level=i%9+1,gender='male' if i%2==0 else 'female',phone='13800138'+str(i).zfill(3))
    if i<10:
        People(admin,clock).review(user,'approved')
        People(admin,clock).review_level(user,True,level=i%9+1)
        People(admin,clock).set_groups(user,[group['id']])
        Service(actor,clock).enroll(user,name,day='2026-10-14',number=1)
    users.append(user)
People(admin,clock).set_roles(users[0],['club_admin','member_manager'])
People(admin,clock).set_roles(users[1],['member_manager','event_manager'])
event=Tournaments(admin,clock).create({'kind':'large','format':'round_robin','title':'第十二届周末羽球交流赛与混合双打友谊联赛',
   'keyword':'周末杯','date':'2026-10-18','venue_id':1,'registration_open':'2026-10-14T09:00:00+08:00','registration_close':'2026-10-17T18:00:00+08:00'})
EventManagement(admin,clock).save_channels(event['id'],[b['channels'][0]['id'] for b in bots],lambda ids,vid:None)
# Proxy-specific fixtures contain only synthetic identities. Each viewport gets
# its own target, so canceling one entry never requires resetting shared data.
owner=replace(ctx,actor=users[0],source='callback',is_admin=False)
Service(owner,clock).enroll(users[0],'张三',2,guest_names=['随行甲','随行乙'],day='2026-10-14',number=1)
proxy_event=Tournaments(admin,clock).create({'kind':'large','format':'round_robin','title':'名单与逐条取消浏览器验收赛',
    'keyword':'名单验收','date':'2026-10-18','venue_id':1,'registration_open':'2026-10-14T09:00:00+08:00','registration_close':'2026-10-17T18:00:00+08:00'})
Tournaments(admin,clock).transition(proxy_event['id'],'open_registration')
proxy_widths=(320,360,390,768,844,1280)
proxy_names=[f'赛事同行{width}' for width in proxy_widths]+['保留同行']
Tournaments(owner,clock).enroll(proxy_event['id'],users[0],'张三',len(proxy_names),guest_names=proxy_names)
proxy_targets={str(width):{'id':users[0]+':'+str(index+1),'name':proxy_names[index]} for index,width in enumerate(proxy_widths)}
forms={str(width):create_form(replace(ctx,actor='mobile-registration-'+str(width),source='callback',is_admin=False),clock) for width in (320,360,390,768,844,1280)}
activity_second=None
if os.environ.get('ROBORT_MOBILE_SUITE')=='activity':
    operations=Service(admin,clock)
    activity_second=operations.save_venue({'name':'训练馆 二号场'})['id']
    third=operations.save_venue({'name':'训练馆 三号场'})['id']
    for vid in (activity_second,third):
        operations.save_slots(vid,[{'name':f'训练场 {i+1}','weekdays':list(range(7)),
            'start':f'{8+i//2:02d}:{i%2*30:02d}','end':f'{8+(i+1)//2:02d}:{(i+1)%2*30:02d}'} for i in range(20)])
    operations.enroll(users[0],'张三',day='2026-10-16')
    Service(admin,lambda:datetime(2026,10,13,16,0,tzinfo=TZ)).enroll(users[0],'张三',day='2026-10-13')
# Synthetic future calendar for editing tests; never contacts a public provider.
if os.environ.get('ROBORT_MOBILE_SUITE') in ('activity','availability','schedule'):
    from app.db import connect, dumps
    payload={'year':2027,'papers':['https://www.gov.cn/fixture-only'],'days':[{'date':'2027-01-01','name':'浏览器验收假期','isOffDay':True}]}
    with connect(admin.db_path,True) as db:
        db.execute('INSERT INTO holiday_calendars VALUES(?,?,?,?)',(2027,dumps(payload),'fixture://synthetic',clock().isoformat()))
(base/'fixture.json').write_text(json.dumps({'run_id':run_id,'origin':origin,'clock':clock().isoformat(),'state':str(state),'password':password,'forms':forms,'event_id':event['id'],'users':users,'bot_ids':[b['id'] for b in bots],
    'activity_second':activity_second,'proxy_event_id':proxy_event['id'],'proxy_targets':proxy_targets,'proxy_owner_id':users[0]+':0','proxy_keep_id':users[0]+':7'},ensure_ascii=False),encoding='utf-8')

@app.get('/__mobile_fixture_ready')
def fixture_ready():
    return {'run_id':run_id}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=port,access_log=False)
