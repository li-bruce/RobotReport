"""Edit and preview synthetic robot copy without sending a real group message."""
import json, os
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta=json.loads((base/'fixture.json').read_text(encoding='utf8'))
origin=meta['origin'];report={'checks':[],'errors':[],'external_requests':[]}

def check(name,value):
    report['checks'].append({'name':name,'passed':bool(value)})
    assert value,name

def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()

try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        for width,height in ((390,844),(1280,900)):
            context=browser.new_context(viewport={'width':width,'height':height})
            context.route('**/*',intercept);page=context.new_page()
            page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.goto(origin+'/admin#bots')
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            first=page.locator('.robot-manager>.robot-card').first
            first.locator(':scope>summary').click();first.locator('.bot-inline-form').wait_for()
            panel=first.locator('.bot-dialogue');panel.locator(':scope>summary').click()
            form=panel.locator('form');form.wait_for()
            form.locator('[data-dialogue-output] span[style*=color] strong').first.wait_for()
            check('inline without navigation',page.url.endswith('#bots'))
            check('no literal style tags','<b>' not in form.locator('[data-dialogue-output]').inner_text())
            check('name and actual bot example','小林' in form.locator('[data-dialogue-output]').inner_text())
            id=first.get_attribute('data-bot-id');others=context.request.get(origin+'/api/bots').json()['bots']
            second=page.locator('.robot-manager>.robot-card').nth(1);second.locator(':scope>summary').click()
            second.locator('.bot-dialogue>summary').click();second.locator('.bot-dialogue form').wait_for()
            check('unique fields across robots',page.evaluate('(()=>{const ids=[...document.querySelectorAll("[id]")].map(n=>n.id);return ids.length===new Set(ids).size})()'))
            second.locator(':scope>summary').click()
            other=next(b for b in others if b['id']!=id)
            before=context.request.get(origin+f"/api/bots/{other['id']}/dialogue").json()
            template=form.locator('[data-dialogue-text]');original=template.input_value()
            form.locator('[name=dialogue_system]').fill('社区活动中心')
            template.fill(original.replace('请发送','欢迎发送'))
            form.locator('[data-dialogue-preview]').click()
            form.locator('[data-dialogue-output]').filter(has_text='社区活动中心').wait_for()
            check('custom preview', '欢迎发送' in form.locator('[data-dialogue-output]').inner_text())
            form.locator('[data-dialogue-select]').select_option('entry.member')
            form.locator('[data-dialogue-select]').select_option('entry.unregistered')
            check('switch preserves unsaved draft','欢迎发送' in template.input_value())
            form.locator('[data-dialogue-search]').fill('不可能存在的场景abcd')
            check('empty search hides stale editor',not form.locator('.dialogue-columns').is_visible())
            form.locator('[data-dialogue-search]').fill('')
            with page.expect_response(lambda r:r.url.endswith('/dialogue') and r.request.method=='PUT') as result:
                form.get_by_role('button',name='保存回复文案',exact=True).click()
            check('save accepted',result.value.status==200)
            form.locator('[data-save-status]').filter(has_text='已保存').wait_for()
            check('other robot unchanged',context.request.get(origin+f"/api/bots/{other['id']}/dialogue").json()==before)
            check('viewport fits',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
            screenshot_style='.topbar,.toast,.skip-link{visibility:hidden!important}'
            panel.screenshot(path=str(base/f'dialogue-{width}.png'),style=screenshot_style)
            if width==1280:
                panel.screenshot(path=str(Path(__file__).parents[2]/'app/static/guide-images/admin-dialogue.png'),style=screenshot_style)
            page.reload();first=page.locator('.robot-manager>.robot-card').first
            first.locator(':scope>summary').click();panel=first.locator('.bot-dialogue');panel.locator(':scope>summary').click();form=panel.locator('form');form.wait_for()
            template=form.locator('[data-dialogue-text]')
            check('reload persisted',template.input_value()!=original and '欢迎发送' in template.input_value())
            form.locator('[data-dialogue-reset]').click()
            check('reset current default',template.input_value()==original)
            form.locator('[name=dialogue_system]').fill('聚场')
            with page.expect_response(lambda r:r.url.endswith('/dialogue') and r.request.method=='PUT') as result:
                form.get_by_role('button',name='保存回复文案',exact=True).click()
            check('reset persisted',result.value.status==200)
            form.locator('[data-dialogue-select]').select_option('enrollment.success')
            form.locator('[data-dialogue-image]').wait_for(state='visible')
            check('table is schematic',form.locator('[data-dialogue-image]').get_attribute('src').endswith('dialogue-table.svg'))
            form.locator('[data-dialogue-select]').select_option('entry.unregistered')
            template.fill('不含必要变量')
            with page.expect_response(lambda r:r.url.endswith('/dialogue') and r.request.method=='PUT') as result:
                form.get_by_role('button',name='保存回复文案',exact=True).click()
            check('bad template rejected',result.value.status==400)
            form.locator('.inline-error').filter(has_text='变量').wait_for()
            check('draft retained after rejection',template.input_value()=='不含必要变量')
            context.close()
        browser.close()
    check('no script errors',not report['errors'])
    check('no external requests',not report['external_requests'])
finally:
    (base/'dialogue-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'checks':len(report['checks']),'path':str(base)},ensure_ascii=False))
