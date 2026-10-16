"""End-to-end robot onboarding against a disposable loopback fixture only."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

output=Path(os.environ['ROBORT_MOBILE_RUN_DIR']).resolve()
meta=json.loads((output/'fixture.json').read_text(encoding='utf-8'))
assert meta['run_id']==os.environ['ROBORT_MOBILE_RUN_ID']
origin=meta['origin']
report={'viewports':[],'checks':[],'failures':[],'page_errors':[],'external_requests':[],'screenshots':[]}

def check(label,passed,detail=None):
    row={'check':label,'passed':bool(passed),'detail':detail};report['checks'].append(row)
    if not passed:report['failures'].append(row)

def get(context,path):
    response=context.request.get(origin+path)
    assert response.ok,(path,response.status)
    return response.json()

def fits(page,label):
    size=page.evaluate('()=>({width:innerWidth,doc:document.documentElement.scrollWidth,body:document.body.scrollWidth})')
    check(label+' no overflow',max(size['doc'],size['body'])<=size['width']+1,size)
    modal=page.locator('#modal[open]')
    if modal.count():
        b=modal.bounding_box();v=page.viewport_size
        check(label+' dialog fits',b['x']>=0 and b['y']>=0 and b['x']+b['width']<=v['width']+1 and b['y']+b['height']<=v['height']+1,b)

def save(page,label):
    button=page.locator('#modal').get_by_role('button',name=label,exact=True)
    form=button.evaluate("el=>el.closest('form').id")
    with page.expect_response(lambda r:r.request.method in ('POST','PATCH','DELETE') and '/api/' in r.url) as sent:button.click()
    assert sent.value.ok,sent.value.text()
    page.locator('#'+form).wait_for(state='detached')

def screenshot(page,name):
    if not page.locator('#modal[open]').count():
        while page.get_by_role('button',name='关闭通知',exact=True).count():page.get_by_role('button',name='关闭通知',exact=True).first.click()
    shot=output/(name+'.png');page.screenshot(path=str(shot),full_page=True);report['screenshots'].append(str(shot))

with sync_playwright() as pw:
    executable=os.environ.get('ROBORT_BROWSER_EXECUTABLE','').strip()
    browser=pw.chromium.launch(headless=True,**({'executable_path':executable} if executable else {}))
    try:
        for width,height in ((320,740),(390,844),(844,390),(1280,900)):
            label=f'{width}x{height}';report['viewports'].append(label)
            context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<1000,has_touch=width<1000)
            def intercept(route):
                if route.request.url.startswith(origin+'/'):route.continue_()
                else:report['external_requests'].append(route.request.url);route.abort()
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda err:report['page_errors'].append(str(err)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#bots');page.wait_for_load_state('networkidle')
            page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('.robot-card').first.wait_for();page.wait_for_load_state('networkidle')
            fits(page,label+' list')
            check(label+' initially collapsed',page.locator('.robot-card[open]').count()==0)
            check(label+' no history or maintenance clutter',page.get_by_text('已移除的群',exact=True).count()==0 and page.get_by_text('连接详情与维护',exact=True).count()==0)
            shared=page.locator('.robot-cluster[data-chat-id="mobile-fixture-group"]')
            check(label+' same group multiple robots',shared.locator('.robot-card').count()>=2)
            shared.locator('.robot-cluster-summary').focus();page.keyboard.press('Enter')
            check(label+' keyboard collapses group',not shared.locator('.robot-cluster-body').is_visible());page.keyboard.press('Enter')
            check(label+' keyword time in robot header','17:30' in shared.locator('.robot-summary-scope').first.inner_text())
            check(label+' business in robot header',shared.locator('.robot-card-summary .robot-summary-business').first.is_visible())
            baseline=get(context,'/api/channels'); baseline=[c for c in baseline if c['bot_id'] in meta['bot_ids']]

            page.get_by_role('button',name='新建群',exact=True).click()
            page.get_by_label('群名称',exact=True).fill('学生新群 '+label)
            check(label+' group only needs name',page.locator('#group-form input').count()==1)
            fits(page,label+' new group');save(page,'保存')
            groups=get(context,'/api/groups')['groups']
            group=next(g for g in groups if g['name']=='学生新群 '+label)
            check(label+' group starts without platform ID',group['chat_id']=='')
            cluster=page.locator(f'.robot-cluster[data-group-id="{group["id"]}"]')
            cluster.get_by_role('button',name='添加机器人',exact=True).click()
            page.get_by_label('机器人名称',exact=True).fill('学生报名助手 '+label)
            daily=page.locator('#bot-form input[value=daily_events]')
            check(label+' daily default checked and editable',daily.is_checked() and daily.is_enabled())
            for key in ('ai','large_events','management'):check(label+' '+key+' default off',not page.locator(f'#bot-form input[value={key}]').is_checked())
            for key in ('registration','enrollment','match_records','statistics','messages'):check(label+' '+key+' default on',page.locator(f'#bot-form input[value={key}]').is_checked())
            daily.uncheck();fits(page,label+' new robot');save(page,'创建并连接群')
            page.locator('#channel-form').wait_for()
            check(label+' shortcut selects existing named group',page.get_by_label('所属群',exact=True).input_value()==group['id'])
            check(label+' no manual group ID or key',page.locator('#channel-form [name=chat_id],#channel-form [name=robot_key],#channel-form [name=name]').count()==0)
            check(label+' only two connection address fields',page.locator('#channel-form input,#channel-form textarea').count()==2)
            callback=page.get_by_label('机器人回调地址',exact=True).input_value()
            check(label+' independent callback available','/kingsoft/callback/' in callback)
            key='browser-student-'+str(width)
            page.get_by_label('发送 Webhook',exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key='+key)
            fits(page,label+' connection');screenshot(page,'connection-'+label)
            with page.expect_response(lambda r:r.url.endswith('/connection/test') and r.request.method=='POST') as tested:
                page.get_by_role('button',name='保存并测试发送',exact=True).click()
            check(label+' send test accepted',tested.value.json()['status']=='sent')
            page.locator('#connection-progress').wait_for()
            check(label+' callback also available after testing',page.get_by_label('机器人回调地址',exact=True).input_value()==callback)
            bots=get(context,'/api/bots')['bots'];bot=next(b for b in bots if b['name']=='学生报名助手 '+label)
            cid=bot['channels'][0]['id'];card=page.locator(f'.robot-card[data-bot-id="{bot["id"]}"]')
            check(label+' daily choice saved','daily_events' not in bot['capabilities'])
            check(label+' pending until callback',bot['channels'][0]['awaiting_callback'])
            body={'chatid':'browser-group-'+str(width),'creator':'browser-member-'+str(width),'content':'帮助','ctime':str(width),'robot_key':key}
            check(label+' real HTTP callback accepted',context.request.post(callback,data=body).status==200)
            page.clock.run_for(3100)
            page.wait_for_function("document.querySelector('#connection-status')?.textContent.includes('已连接')")
            check(label+' connected only after callback','已连接' in page.locator('#connection-status').inner_text())
            page.locator('#modal .modal-close').click()
            # Production UI polls pending discovery; advance the installed clock.
            page.clock.run_for(5100)
            page.locator(f'.robot-cluster[data-chat-id="browser-group-{width}"]').wait_for()
            check(label+' automatic group identification visible','browser-group-'+str(width) in cluster.locator('.robot-group-id').inner_text())
            card.locator('.robot-card-summary').focus();page.keyboard.press('Space')
            check(label+' robot keyboard expands',card.get_attribute('open') is not None)
            check(label+' no repeated group ID inside robot','browser-group-'+str(width) not in card.inner_text())
            fits(page,label+' expanded');screenshot(page,'bots-expanded-'+label)
            card.get_by_role('button',name='帮助预览',exact=True).click();page.locator('#bot-help-preview').wait_for()
            check(label+' daily hidden from help','组织比赛' not in page.locator('#bot-help-preview').inner_text())
            page.locator('#modal .modal-close').click()
            card.get_by_role('button',name='编辑设置',exact=True).click()
            page.locator('#bot-form input[value=daily_events]').check()
            page.locator('.robot-scope-settings > summary').click()
            page.get_by_label('场次范围',exact=True).select_option('selected')
            page.locator('#bot-form input[name=slot_keys]').first.check()
            page.locator('.robot-user-settings > summary').click()
            page.get_by_label('启用用户条件',exact=True).check()
            page.get_by_role('button',name='添加条件',exact=True).click()
            rule=page.locator('.robot-rule').first
            rule.get_by_label('用户属性',exact=True).select_option('gender')
            rule.get_by_label('女',exact=True).check()
            page.get_by_role('button',name='添加条件',exact=True).click()
            rule=page.locator('.robot-rule').nth(1)
            rule.get_by_label('用户属性',exact=True).select_option('level')
            rule.get_by_label('匹配方式',exact=True).select_option('gte')
            rule.get_by_label('取值',exact=True).fill('3')
            fits(page,label+' user conditions');screenshot(page,'user-conditions-'+label)
            save(page,'保存设置')
            saved=next(b for b in get(context,'/api/bots')['bots'] if b['id']==bot['id'])['user_policy']
            check(label+' typed conditions persisted',saved=={'enabled':True,'match':'all','rules':[{'field':'gender','op':'in','value':['female']},{'field':'level','op':'gte','value':3}]})
            card.get_by_role('button',name='编辑设置',exact=True).click()
            page.locator('.robot-user-settings > summary').click()
            check(label+' user conditions reopen',page.locator('.robot-rule').count()==2)
            page.get_by_label('启用用户条件',exact=True).uncheck()
            save(page,'保存设置')
            check(label+' newly enabled daily badge in header','每日赛事' in card.locator('.robot-summary-business').inner_text())
            check(label+' selected slot time in header','17:30' in card.locator('.robot-summary-scope').inner_text())
            card.get_by_role('button',name='编辑连接',exact=True).click()
            check(label+' current outgoing address restored',page.get_by_label('发送 Webhook',exact=True).input_value().endswith('key='+key))
            known=next(g for g in get(context,'/api/groups')['groups'] if g['chat_id']=='mobile-fixture-group')
            page.get_by_label('所属群',exact=True).select_option(known['id'])
            check(label+' moving group clears credentials',page.get_by_label('发送 Webhook',exact=True).input_value()=='')
            next_key='browser-rebound-'+str(width)
            page.get_by_label('发送 Webhook',exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key='+next_key)
            save(page,'保存连接')
            after=get(context,f'/api/bots/{bot["id"]}/channels')
            check(label+' moved group gets fresh pending connection',len(after)==1 and after[0]['id']!=cid and after[0]['awaiting_callback'])
            check(label+' old callback rejected',context.request.post(callback,data=body).status==403)
            check(label+' wrong selected group rejected',context.request.post(callback,data={**body,'robot_key':next_key}).status==403)
            check(label+' shared group own callback accepted',context.request.post(callback,data={**body,'robot_key':next_key,'chatid':'mobile-fixture-group','ctime':str(width+1)}).status==200)
            page.clock.run_for(5100);page.reload();page.locator('.robot-card').first.wait_for()
            card.locator('.robot-card-summary').click()
            with page.expect_response(lambda r:r.request.method=='PATCH' and '/channels/' in r.url):card.get_by_role('button',name='活动提醒 · 开',exact=True).click()
            card.get_by_role('button',name='活动提醒 · 关',exact=True).wait_for()
            with page.expect_response(lambda r:r.request.method=='PATCH' and '/channels/' in r.url):card.get_by_role('button',name='暂停连接',exact=True).click()
            card.get_by_role('button',name='启用连接',exact=True).wait_for()
            check(label+' paused connection saved',not get(context,f'/api/bots/{bot["id"]}/channels')[0]['active'])
            with page.expect_response(lambda r:r.request.method=='PATCH' and '/channels/' in r.url):card.get_by_role('button',name='启用连接',exact=True).click()
            card.get_by_role('button',name='暂停连接',exact=True).wait_for()
            check(label+' other robots unchanged',all(c in get(context,'/api/channels') for c in baseline))
            fits(page,label+' final');screenshot(page,'bots-'+label)
            card.get_by_role('button',name='解除连接',exact=True).click()
            save(page,'解除连接')
            check(label+' removed current connection absent',get(context,f'/api/bots/{bot["id"]}/channels')==[])
            history=get(context,f'/api/bots/{bot["id"]}/channels?include_archived=true')
            check(label+' archived history retained',len(history)==2 and all(c['archived'] for c in history))
            cluster.get_by_role('button',name='删除群',exact=True).click();save(page,'删除群')
            check(label+' delete group with history removes it from list',not any(g['id']==group['id'] for g in get(context,'/api/groups')['groups']))
            check(label+' deleting group retains robot',any(b['id']==bot['id'] for b in get(context,'/api/bots')['bots']))
            card.locator('.robot-card-summary').click()
            card.get_by_role('button',name='删除机器人',exact=True).click();save(page,'删除机器人')
            check(label+' unbound robot deleted',not any(b['id']==bot['id'] for b in get(context,'/api/bots')['bots']))
            check(label+' deleted callback disabled',context.request.post(callback,data=body).status==403)
            check(label+' other connections survive delete',all(c in get(context,'/api/channels') for c in baseline))
            context.close()
    except Exception as error:
        report['failures'].append({'check':'browser run','detail':str(error)})
        raise
    finally:
        browser.close();check('no page errors',not report['page_errors'],report['page_errors']);check('no external requests',not report['external_requests'],report['external_requests'])
        (output/'bots-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'checks':len(report['checks']),'failures':len(report['failures']),'report':str(output/'bots-report.json')},ensure_ascii=False))
raise SystemExit(1 if report['failures'] else 0)
