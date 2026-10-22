"""Robot tabs, connection graph and roster matching on disposable data."""
import io
import json
import os
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from playwright.sync_api import sync_playwright

output=Path(os.environ['ROBORT_MOBILE_RUN_DIR']).resolve()
meta=json.loads((output/'fixture.json').read_text(encoding='utf-8'))
assert meta['run_id']==os.environ['ROBORT_MOBILE_RUN_ID']
origin=meta['origin']
report={'viewports':[],'checks':[],'failures':[],'page_errors':[],'external_requests':[],'screenshots':[]}


def check(label,passed,detail=None):
    row={'check':label,'passed':bool(passed),'detail':detail}
    report['checks'].append(row)
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
        box=modal.bounding_box();viewport=page.viewport_size
        check(label+' dialog fits',box['x']>=0 and box['y']>=0 and box['x']+box['width']<=viewport['width']+1 and box['y']+box['height']<=viewport['height']+1,box)


def save(page,label):
    button=page.locator('#modal').get_by_role('button',name=label,exact=True)
    form=button.evaluate("el=>el.closest('form').id")
    with page.expect_response(lambda r:r.request.method in ('POST','PATCH','DELETE') and '/api/' in r.url) as sent:
        button.click()
    assert sent.value.ok,sent.value.text()
    page.locator('#'+form).wait_for(state='detached')


def screenshot(page,name):
    shot=output/(name+'.png')
    page.screenshot(path=str(shot),full_page=True)
    report['screenshots'].append(str(shot))


def upload(page,rows):
    book=Workbook();sheet=book.active;sheet.title='会员'
    for row in rows:sheet.append(row)
    stream=io.BytesIO();book.save(stream);book.close()
    page.locator('#member-import-file').set_input_files({
        'name':'会员.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':stream.getvalue()})
    with page.expect_response(lambda r:r.url.endswith('/member-imports/preview')) as response:
        page.locator('#member-import-upload [type=submit]').click()
    assert response.value.ok,response.value.text()
    page.locator('#member-import-commit').wait_for()
    return response.value.json()


def resolve(page,button):
    with page.expect_response(lambda r:r.url.endswith('/member-imports/matches')) as response:button.click()
    assert response.value.ok,response.value.text()
    page.wait_for_function("document.querySelector('#member-import-preview')?.dataset.busy!=='true'")
    return response.value.json()


def commit(page):
    with page.expect_response(lambda r:r.url.endswith('/member-imports/commit')) as response:
        page.locator('#member-import-commit [type=submit]').click()
    assert response.value.ok,response.value.text()
    page.locator('.import-complete').wait_for()
    return response.value.json()


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
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('.robot-card').first.wait_for();page.wait_for_load_state('networkidle')
            fits(page,label+' management')
            check(label+' cards initially collapsed',page.locator('.robot-card[open]').count()==0)
            check(label+' connection controls separated',page.locator('#main [data-action=channel-edit]').count()==0)
            check(label+' scope and capabilities in header','17:30' in page.locator('.robot-summary-times').first.inner_text() and page.locator('.robot-summary-business').first.is_visible())
            baseline=[c for c in get(context,'/api/channels') if c['bot_id'] in meta['bot_ids']]
            page.locator('#main').get_by_role('link',name='群关联',exact=True).click()
            page.locator('.relation-viewport').wait_for()
            check(label+' shared group multiple edges',page.locator('.relation-edge').count()>=2)
            fits(page,label+' graph');screenshot(page,'graph-'+label)
            page.get_by_role('button',name='缩小连接图',exact=True).click()
            check(label+' zoom changes canvas','scale(' in page.locator('.relation-canvas').get_attribute('style'))
            page.get_by_role('button',name='适应宽度',exact=True).click()
            edge=page.locator('.relation-edge').first;edge.focus();page.keyboard.press('Enter')
            page.locator('#channel-form').wait_for()
            check(label+' edge keyboard opens connection',page.get_by_label('所属群',exact=True).input_value()!='')
            check(label+' no manual IDs or keys',page.locator('#channel-form [name=chat_id],#channel-form [name=robot_key]').count()==0)
            fits(page,label+' connection');page.locator('#modal .modal-close').click()
            page.get_by_role('button',name='新建群',exact=True).click()
            page.get_by_label('群名称',exact=True).fill('图示新群 '+label);save(page,'保存')
            group=next(g for g in get(context,'/api/groups')['groups'] if g['name']=='图示新群 '+label)
            check(label+' group without platform ID',group['chat_id']=='')
            page.locator('#main').get_by_role('link',name='机器人管理',exact=True).click()
            page.get_by_role('button',name='新建机器人',exact=True).click()
            page.get_by_label('机器人名称',exact=True).fill('测试报名助手 '+label)
            check(label+' daily default on',page.locator('#bot-form input[value=daily_events]').is_checked())
            check(label+' AI management large events default off',all(not page.locator(f'#bot-form input[value={key}]').is_checked() for key in ('ai','management','large_events')))
            page.locator('#bot-form input[value=daily_events]').uncheck();save(page,'创建机器人')
            bot=next(b for b in get(context,'/api/bots')['bots'] if b['name']=='测试报名助手 '+label)
            card=page.locator(f'.robot-card[data-bot-id="{bot["id"]}"]')
            card.locator('summary').focus();page.keyboard.press('Space')
            card.get_by_role('button',name='帮助预览',exact=True).click()
            page.locator('#bot-help-preview').wait_for()
            check(label+' help follows enabled features','组织比赛' not in page.locator('#bot-help-preview').inner_text())
            page.locator('#modal .modal-close').click()
            page.locator('#main .section-tabs').get_by_role('link',name='群关联',exact=True).click()
            node=page.locator(f'.relation-bot[data-bot-id="{bot["id"]}"]');node.click()
            page.get_by_label('所属群',exact=True).select_option(group['id'])
            callback=page.get_by_label('机器人回调地址',exact=True).input_value()
            key='browser-fixture-'+str(width)
            page.get_by_label('发送 Webhook',exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key='+key)
            with page.expect_response(lambda r:r.url.endswith('/connection/test')) as tested:
                page.get_by_role('button',name='保存并测试发送',exact=True).click()
            check(label+' local mock accepts send',tested.value.json()['status']=='sent')
            page.locator('#connection-progress').wait_for()
            body={'chatid':'browser-group-'+str(width),'creator':'browser-user-'+str(width),'content':'连接','ctime':str(width),'robot_key':key}
            check(label+' correct callback accepted',context.request.post(callback,data=body).status==200)
            page.locator('#connection-progress').get_by_role('button',name='刷新状态',exact=True).click()
            page.wait_for_function("document.querySelector('#connection-status')?.textContent.includes('已连接')")
            page.locator('#modal .modal-close').click()
            node.wait_for()
            page.wait_for_function("(width)=>document.querySelector('.relation-group-list')?.textContent.includes('browser-group-'+width)",arg=width)
            node.click();page.get_by_label('发送活动提醒',exact=True).uncheck();save(page,'保存连接')
            channel=get(context,f'/api/bots/{bot["id"]}/channels')[0]
            check(label+' notification setting persisted',not channel['daily_notifications'])
            node.click();page.get_by_label('启用连接',exact=True).uncheck();save(page,'保存连接')
            check(label+' paused callback denied',context.request.post(callback,data={**body,'ctime':str(width+1)}).status==403)
            node.click();page.get_by_label('启用连接',exact=True).check()
            page.get_by_label('发送 Webhook',exact=True).fill('')
            save(page,'保存连接')
            check(label+' receive-only callback reply',context.request.post(callback,data={**body,'ctime':str(width+2)}).json().get('reply'))
            page.reload();page.locator('.relation-viewport').wait_for()
            check(label+' receive-only link label','仅接收' in page.locator(f'.relation-edge-label[data-bot-id="{bot["id"]}"]').inner_text())
            node.click();page.get_by_role('button',name='解除连接',exact=True).click();save(page,'解除连接')
            check(label+' removed connection archived',not get(context,f'/api/bots/{bot["id"]}/channels') and len(get(context,f'/api/bots/{bot["id"]}/channels?include_archived=true'))==1)
            item=page.locator('.relation-group-item').filter(has_text='图示新群 '+label)
            item.get_by_role('button',name='编辑',exact=True).click()
            page.get_by_label('群名称',exact=True).fill('更名新群 '+label);save(page,'保存')
            page.locator('.relation-group-item').filter(has_text='更名新群 '+label).get_by_role('button',name='删除',exact=True).click();save(page,'删除群')
            page.locator('#main .section-tabs').get_by_role('link',name='机器人管理',exact=True).click()
            if not card.get_attribute('open'):card.locator('summary').click()
            card.get_by_role('button',name='删除机器人',exact=True).click();save(page,'删除机器人')
            check(label+' other robots unchanged',all(c in get(context,'/api/channels') for c in baseline))

            page.goto(origin+'/admin#member-imports');page.locator('#member-import-file').wait_for()
            name='同名会员'+str(width)
            preview=upload(page,[['姓名','学号','性别'],[name,'A'+str(width),'男'],[name,'B'+str(width),'女'],['单人会员'+str(width),'C'+str(width),'女']])
            check(label+' same file duplicate blocked',not preview['can_commit'] and page.locator('#member-import-commit [type=submit]').is_disabled())
            resolve(page,page.get_by_role('button',name='不匹配，新增',exact=True))
            check(label+' explicit different person import',commit(page)['imported']==3)
            page.get_by_role('button',name='继续导入',exact=True).click()
            preview=upload(page,[['人员姓名','学生编号','等级'],[name,'B'+str(width),5],['改名会员'+str(width),'C'+str(width),6],['新会员'+str(width),'D'+str(width),1]])
            check(label+' name and ID candidates',preview['match_count']==2)
            cards=page.locator('.import-match-card')
            check(label+' ambiguous candidate not selected',cards.first.locator('input:checked').count()==0)
            check(label+' candidate basis shown','匹配依据' in cards.first.inner_text())
            fits(page,label+' matching');screenshot(page,'import-matching-'+label)
            result=resolve(page,page.get_by_role('button',name='批量确认匹配（1）',exact=True))
            check(label+' bulk only unique candidates',result['match_count']==1)
            page.locator('.import-match-card input[type=radio]').first.check()
            result=resolve(page,page.get_by_role('button',name='确认匹配',exact=True))
            check(label+' all confirmations unlock import',result['can_commit'] and result['skip_count']==2)
            check(label+' commits only genuinely new',commit(page)['imported']==1)
            fits(page,label+' import complete')
            context.close()
    except Exception as error:
        report['failures'].append({'check':'browser run','detail':str(error)})
        raise
    finally:
        browser.close()
        check('no page errors',not report['page_errors'],report['page_errors'])
        check('no external requests',not report['external_requests'],report['external_requests'])
        (output/'bots-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'checks':len(report['checks']),'failures':len(report['failures']),'report':str(output/'bots-report.json')},ensure_ascii=False))
raise SystemExit(1 if report['failures'] else 0)
