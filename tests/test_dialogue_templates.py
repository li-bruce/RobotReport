import asyncio
from dataclasses import replace
from datetime import datetime
import html
import json
import re

import pytest
from fastapi import HTTPException

from app.db import TZ, connect, dumps
from app.security import Platform
from app.commands import Commands
from app.members import Members
from app.dialogue import (TEMPLATES, validate, render, preview, settings_api, entry,
                          command, Markup, MembershipPrompt)


def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', '', value)).replace('**','')


@pytest.fixture
def club(tmp_path):
    platform=Platform(tmp_path,'template-admin-token-of-at-least-32-characters')
    admin=platform.authenticate('template-admin-token-of-at-least-32-characters')
    bots=[platform.create(admin,{'name':name,'chat_id':'test-group','capabilities':['registration','enrollment','daily_events']})
          for name in ('喂 - 一组专用','喂 - 二组专用')]
    ctx=platform.context(admin,bots[0]['id'])
    clock=lambda:datetime(2026,11,9,12,tzinfo=TZ)
    user=replace(ctx,actor='12345',is_admin=False,permissions=(),source='callback')
    return platform,admin,bots,ctx,user,clock


def test_every_template_valid_and_previewable(club):
    p,a,b,*_=club
    for key,spec in TEMPLATES.items():
        assert validate(key,spec.text)==spec.text
        reply=preview(p,a,b[0]['id'],{'id':key,'text':spec.text})
        assert reply['reply'] and not re.search(r'\{[a-z_]+\}',reply['reply']),key
        assert reply['image']==spec.image


def test_personalized_bare_mention_and_membership_gate(club):
    p,a,b,ctx,user,clock=club
    router=Commands(user,clock)
    reply=asyncio.run(router.run('@喂 - 一组专用','小林'))
    assert '小林，您好' in reply and '尚未注册' in reply
    assert '@喂 - 一组专用 注册' in plain(reply)
    assert '<font color="#0066b2"><b>' in reply
    assert len(plain(reply))<200 and '12345' not in reply
    with pytest.raises(MembershipPrompt) as denied:
        asyncio.run(router.run('报名','小林'))
    assert '小林，您好' in str(denied.value)
    Members(ctx,clock).review('12345','approved')
    reply=asyncio.run(router.run('@喂 - 一组专用','小林'))
    assert '@喂 - 一组专用 注册' in plain(reply) and '@喂 - 一组专用 报名' in plain(reply)
    assert 'AI' not in reply and '审核通过' not in reply and len(plain(reply))<300


def test_identity_is_escaped_and_fallback_never_displays_account_id(club):
    *_,ctx,user,clock=club
    reply=entry(user,{'status':'unregistered','name':'<script>x</script>'})
    assert '<script>' not in reply and '&lt;script&gt;' in reply
    assert '12345' not in entry(user,{'status':'unregistered','name':'12345'})
    bot=replace(user,bot_name='A <b>& "机器人')
    assert '@A &lt;b&gt;&amp; &quot;机器人 注册' in command(bot,'注册')


def test_saving_is_bot_local_durable_and_conflict_checked(club):
    p,a,b,ctx,user,clock=club
    before=settings_api(p,a,b[0]['id'])
    custom=TEMPLATES['entry.unregistered'].text.replace('请发送','欢迎发送')
    saved=settings_api(p,a,b[0]['id'],{'revision':before['revision'],'system_name':'社区活动','overrides':{'entry.unregistered':custom}})
    reply=asyncio.run(Commands(user,clock).run('@喂 - 一组专用','小林'))
    assert '社区活动' in reply and '欢迎发送' in reply
    other=replace(user,id=b[1]['id'],bot_name=b[1]['name'])
    assert '社区活动' not in entry(other,{'status':'unregistered'})
    reopened=Platform(p.root)
    assert settings_api(reopened,a,b[0]['id'])['overrides']['entry.unregistered']==custom
    with pytest.raises(HTTPException) as conflict:
        settings_api(p,a,b[0]['id'],{'revision':before['revision'],'system_name':'旧设置','overrides':{}})
    assert conflict.value.status_code==409
    settings_api(p,a,b[0]['id'],{'revision':saved['revision'],'system_name':'聚场','overrides':{}})
    assert '欢迎发送' not in entry(user,{'status':'unregistered'})


@pytest.mark.parametrize('bad',[
    '<script>{greeting}{system_name}{register_command}</script>',
    '<font color="red" onclick="alert(1)">{greeting}{system_name}{register_command}</font>',
    '<font color="#0066b2">{greeting}{system_name}{register_command}',
    '{greeting.__class__}{system_name}{register_command}',
    '{greeting!r}{system_name}{register_command}',
    '{greeting}{system_name}',
    '{greeting}{system_name}{register_command}[去注册](javascript:alert)',
    '{greeting}{system_name}{register_command}'+('文'*500),
])
def test_unsafe_or_incomplete_template_is_rejected(bad):
    with pytest.raises(ValueError):validate('entry.unregistered',bad)


def test_registration_disabled_and_pending_states_do_not_advertise_signup(club):
    p,a,b,ctx,user,clock=club
    disabled=replace(user,capabilities=('statistics',),platform_root=None)
    assert '@' not in plain(entry(disabled,{'status':'unregistered'}))
    assert '不办理' in plain(entry(disabled,{'status':'pending'})) or '负责注册' in plain(entry(disabled,{'status':'pending'}))
    pending=entry(user,{'status':'pending','name':'小林'})
    assert '等待审核' in pending and '注册资料' in pending and '报名' not in pending


def test_template_edit_requires_platform_admin(club):
    p,a,b,ctx,user,clock=club
    grant=p.grant(a,[b[0]['id']]);limited=p.authenticate(grant['token'])
    with pytest.raises(PermissionError):settings_api(p,limited,b[0]['id'])


def test_fact_variables_cannot_disappear(club):
    p,a,b,ctx,user,clock=club
    with pytest.raises(ValueError):validate('enrollment.success','已报名')
    value=render(user,'enrollment.success',heading=Markup('**第一场**'),table=Markup('| 姓名 |\n| --- |\n| 小林 |'))
    assert '| 小林 |' in value


def test_http_editor_requires_auth_and_rejects_stale_writes(club):
    from fastapi.testclient import TestClient
    from app.main import create_app
    p,a,b,ctx,user,clock=club
    token='template-admin-token-of-at-least-32-characters'
    path=f"/api/bots/{b[0]['id']}/dialogue"
    with TestClient(create_app(p.root,token,False,False,clock)) as client:
        assert client.get(path).status_code==401
        headers={'Authorization':'Bearer '+token}
        saved=client.get(path,headers=headers).json()
        body={k:saved[k] for k in ('revision','system_name','overrides')}
        body['system_name']='邻里活动'
        result=client.put(path,headers=headers,json=body)
        assert result.status_code==200,result.text
        assert client.put(path,headers=headers,json=body).status_code==409
        preview_result=client.post(path+'/preview',headers=headers,json={'id':'entry.unregistered','text':TEMPLATES['entry.unregistered'].text})
        assert preview_result.status_code==200
        assert '@喂 - 一组专用 注册' in preview_result.json()['reply']
        body['revision']=result.json()['revision'];body['overrides']={'entry.unregistered':'无变量'}
        assert client.put(path,headers=headers,json=body).status_code==400


def test_bare_mention_obeys_current_feature_hours(club):
    p,a,b,ctx,user,clock=club
    Members(ctx,clock).touch('小林','12345',source='admin');Members(ctx,clock).review('12345','approved')
    p.update(a,b[0]['id'],{'capability_rules':[{'id':'night','name':'晚间报名','enabled':True,
        'capabilities':['enrollment'],'action':'open','when':{'start_time':'17:00','end_time':'21:00'}}]})
    reply=asyncio.run(Commands(user,clock).run('@喂 - 一组专用'))
    assert '@喂 - 一组专用 注册' in reply
    assert '@喂 - 一组专用 报名' not in reply and '@喂 - 一组专用 名单' not in reply


def test_identity_notice_uses_origin_robot_template_and_mention(club):
    from app.identity_notifications import IdentityNotifications
    from app.service import Service
    p,a,b,ctx,user,clock=club
    # Real transport credentials are never used: these are synthetic fixture URLs.
    for i,bot in enumerate(b):
        p.save_channel(a,bot['id'],{'webhook_url':f'https://xz.wps.cn/api/v1/webhook/send?key=dialogue-{i}'},bot['channels'][0]['id'])
        settings=settings_api(p,a,bot['id'])
        text=TEMPLATES['notice.approved'].text.replace('欢迎加入','一号欢迎您加入' if i==0 else '二号欢迎您加入')
        settings_api(p,a,bot['id'],{'revision':settings['revision'],'system_name':'聚场','overrides':{'notice.approved':text}})
    Members(user,clock).touch('小林')
    Members(user,clock).apply('小林',gender='female')
    Members(ctx,clock).review('12345','approved')
    notifications=IdentityNotifications(p,clock)
    with connect(ctx.db_path) as db:
        event_id=db.execute("SELECT id FROM identity_notification_events WHERE user_id='12345' ORDER BY rowid DESC").fetchone()[0]
    notifications.dispatch(event_id)
    with connect(user.transport_db_path) as db:
        text=json.loads(db.execute("SELECT payload FROM outbox ORDER BY rowid DESC").fetchone()[0])['markdown']['text']
    assert '一号欢迎您加入' in text and '二号欢迎您加入' not in text
    assert '<at user_id="12345">小林</at>' in text and '@喂 - 一组专用 帮助' in text


def test_durable_notice_keeps_facts_and_dedupes_after_copy_change(club):
    from app.notifications import Notifications
    from app.messages import Outbox
    p,a,b,ctx,user,clock=club
    bot=b[0];channel_id=bot['channels'][0]['id']
    p.update(a,bot['id'],{'capabilities':['registration','messages']})
    p.save_channel(a,bot['id'],{'webhook_url':'https://xz.wps.cn/api/v1/webhook/send?key=notice-fixture'},channel_id)
    settings=settings_api(p,a,bot['id'])
    custom=TEMPLATES['notice.event_closed'].text.replace('感谢大家报名','谢谢各位参与')
    saved=settings_api(p,a,bot['id'],{'revision':settings['revision'],'system_name':'聚场','overrides':{'notice.event_closed':custom}})
    notices=Notifications(p,clock)
    facts={'event':'周末赛','venue':1,'count':8}
    job=notices.enqueue('fallback',[channel_id],key='fixture-closed',dialogue={'id':'notice.event_closed','values':facts})
    with connect(ctx.transport_db_path) as db:
        prior=db.execute('SELECT payload FROM outbox ORDER BY id DESC').fetchone()[0]
        count=db.execute('SELECT count(*) FROM outbox').fetchone()[0]
    assert '谢谢各位参与' in prior and '8 人' in prior and '周末赛' in prior
    settings_api(p,a,bot['id'],{'revision':saved['revision'],'system_name':'聚场','overrides':{}})
    assert notices.enqueue('changed',[channel_id],key='fixture-closed')['id']==job['id']
    with connect(ctx.transport_db_path) as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==count
        assert db.execute('SELECT payload FROM outbox ORDER BY id DESC').fetchone()[0]==prior
