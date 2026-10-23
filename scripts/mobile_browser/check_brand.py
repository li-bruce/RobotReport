"""GatherSpace homepage and robot-scoped schedule navigation on isolated data."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta=json.loads((base/'fixture.json').read_text(encoding='utf8'))
origin=meta['origin']
report={'checks':[],'errors':[],'external_requests':[]}

def check(name,value):
    report['checks'].append({'name':name,'passed':bool(value)})
    assert value,name

def fit(page,name):
    check(name,page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))

def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()

def config(context):
    response=context.request.get(origin+'/api/config')
    assert response.ok
    return response.json()

def save(page):
    with page.expect_response(lambda r:r.request.method=='PATCH' and r.url.endswith('/api/config')) as response:
        page.locator('#reminder-form [type=submit]').click()
    check('schedule save succeeds',response.value.status==200)
    page.wait_for_load_state('networkidle')
    page.locator('#broadcast-rules').wait_for()

def new_rule(page,name,time,bot):
    page.locator('#broadcast-rules [data-add-rule]').click()
    rule=page.locator('#broadcast-rules [data-time-rule]').last
    check('new rule targets selected robot',rule.locator('[data-rule-choice=bot_ids]:checked').evaluate_all('(nodes)=>nodes.map(n=>n.value)')==[bot])
    rule.locator('[data-rule-field=name]').fill(name)
    rule.locator('[data-rule-field=time]').fill(time)
    rule.locator('[data-rule-choice=kinds][value=open]').uncheck()
    rule.locator('[data-rule-choice=kinds][value=roster]').check()
    return rule

try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        for width,height in ((320,740),(390,844),(768,1024),(844,390),(1280,900),(1920,1080)):
            context=browser.new_context(viewport={'width':width,'height':height})
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#dashboard')
            page.locator('.gather-login').wait_for()
            page.locator('.gather-login').evaluate("el=>new Promise((resolve,reject)=>{const image=new Image();image.onload=resolve;image.onerror=reject;image.src=getComputedStyle(el).backgroundImage.match(/url\([\"']?(.*?)[\"']?\)/)[1];})")
            portrait = width <= 760 or (width <= 1000 and height >= width)
            asset = 'gatherspace-login-mobile.png' if portrait else 'gatherspace-login-desktop.png'
            check(f'{width} selects dedicated background', asset in page.locator('.gather-login').evaluate('el=>getComputedStyle(el).backgroundImage'))
            visible = page.locator('.gather-login-access').evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1;}')
            check(f'{width} login form is fully visible without scrolling',visible)
            check('login product name','聚场' in page.title())
            check('login intro only title and core line',''.join(page.locator('.gather-login-title').inner_text().split())=='聚场GatherSpace场地安排·活动报名·赛事组织·机器人与群')
            check('login uses background instead of picture card',page.locator('.gather-login img,.login-brand,.login-thesis').count()==0)
            check('background fills login viewport',page.locator('.gather-login').evaluate('el=>{const r=el.getBoundingClientRect();return Math.abs(r.left)<1&&Math.abs(r.top)<1&&Math.abs(r.width-innerWidth)<2&&r.height>=innerHeight-1;}'))
            check('core description stays one line',page.locator('.gather-login-functions').evaluate('el=>el.scrollWidth<=el.clientWidth+1&&el.getBoundingClientRect().height<=parseFloat(getComputedStyle(el).lineHeight)+1'))
            check('login guide remains accessible',page.locator('.login-guide a').get_attribute('href')=='/guide/admin')
            fit(page,f'{width} login fits')
            page.screenshot(path=str(base/f'login-{width}.png'),full_page=True)
            if width==320:
                page.get_by_label('管理员密码',exact=True).fill('incorrect-password')
                with page.expect_response(lambda r:r.url.endswith('/auth/login')) as rejected:
                    page.get_by_role('button',name='登录工作台',exact=True).click()
                check('invalid password denied',rejected.value.status==403)
                page.wait_for_function("document.querySelector('#login-form .inline-error').textContent.trim().length>0")
                check('login error shown with form available',page.locator('#login-form [type=submit]').is_enabled())
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('.metric-grid').wait_for()
            check('dashboard business overview restored',page.locator('.operations-strip').count()==1 and page.locator('.session-row').count()>0)
            check('no login backdrop in dashboard',page.locator('.gather-hero,.gather-login').count()==0 and page.locator('#main').evaluate('el=>parseFloat(getComputedStyle(el).paddingLeft)>0'))
            check('workspace is generic','聚场管理后台' in page.locator('.workspace-label').inner_text())
            fit(page,f'{width} dashboard fits')
            page.screenshot(path=str(base/f'dashboard-{width}.png'),full_page=True)
            page.goto(origin+'/admin#bots')
            card=page.locator('.robot-card').first;card.locator(':scope>summary').click()
            card.locator('.bot-inline-form').wait_for()
            check('robot settings embedded',card.locator('.bot-broadcast-panel').is_visible() and page.url.endswith('#bots'))
            fit(page,f'{width} settings fit')
            page.goto(origin+'/admin#venues')
            page.locator('[data-venue-card]').first.wait_for()
            card=page.locator('[data-venue-card]').first
            card.get_by_role('button',name='编辑场地',exact=True).click()
            form=page.locator('#venue-form')
            form.get_by_role('button',name='添加属性',exact=True).click()
            item=form.locator('.venue-attribute-row').last
            item.get_by_label('属性名称',exact=True).fill('运动类型')
            form.get_by_role('button',name='保存场地',exact=True).click()
            check('incomplete annotation stays editable','请补全' in form.locator('.inline-error').inner_text())
            item.get_by_label('属性内容',exact=True).fill('羽毛球、篮球')
            form.get_by_role('button',name='添加属性',exact=True).click()
            item=form.locator('.venue-attribute-row').last
            item.get_by_label('属性名称',exact=True).fill('配套设施')
            item.get_by_label('属性内容',exact=True).fill('储物柜、饮水区')
            fit(page,f'{width} venue form fits')
            check('attribute input fits dialog',form.locator('.venue-attribute-editor').evaluate('el=>el.scrollWidth<=el.clientWidth+1'))
            page.screenshot(path=str(base/f'venue-edit-{width}.png'),full_page=True)
            if width==1280:
                page.locator('#modal').screenshot(path=str(base/'admin-venue-attributes.png'))
            with page.expect_response(lambda r:r.request.method=='PATCH' and '/api/venues/' in r.url) as saved:
                form.get_by_role('button',name='保存场地',exact=True).click()
            check('venue metadata save succeeds',saved.value.status==200)
            page.locator('#modal').wait_for(state='hidden')
            page.reload();page.locator('[data-venue-card]').first.wait_for()
            card=page.locator('[data-venue-card]').first
            check('venue attributes persist after reload',card.locator('.venue-attributes dd').all_text_contents()==['羽毛球、篮球','储物柜、饮水区'])
            fit(page,f'{width} annotated cards fit')
            page.screenshot(path=str(base/f'venues-{width}.png'),full_page=True)
            if width==1280:
                page.locator('#main').screenshot(path=str(base/'admin-venues.png'))
            card.get_by_role('button',name='编辑场地',exact=True).click()
            form=page.locator('#venue-form')
            form.locator('[data-attribute-remove]').last.click()
            form.locator('[data-attribute-value]').fill('<img src=x onerror=alert(1)>')
            form.get_by_role('button',name='保存场地',exact=True).click()
            page.locator('#modal').wait_for(state='hidden');page.wait_for_load_state('networkidle')
            card=page.locator('[data-venue-card]').first
            check('annotation text cannot insert markup',card.locator('.venue-attributes img').count()==0 and '<img src=x' in card.locator('.venue-attributes').inner_text())
            card.get_by_role('button',name='编辑场地',exact=True).click()
            form=page.locator('#venue-form')
            form.locator('[data-attribute-remove]').click()
            form.get_by_role('button',name='保存场地',exact=True).click()
            page.locator('#modal').wait_for(state='hidden');page.wait_for_load_state('networkidle')
            page.reload();page.locator('[data-venue-card]').first.wait_for()
            check('all annotations can be removed',page.locator('[data-venue-card]').first.locator('.venue-attributes').count()==0)
            page.goto(origin+'/admin#settings');page.locator('#main .loading').wait_for(state='hidden');page.wait_for_load_state('networkidle')
            check('system tabs omit broadcast',page.locator('.section-tabs [data-route=reminders]').count()==0)
            page.get_by_role('button',name='退出登录',exact=True).click();page.locator('.gather-login').wait_for()
            check('logout returns to background login',page.locator('#login-form').is_visible())
            context.close()
        browser.close()
except Exception as error:report['errors'].append(str(error))
finally:
    (base/'brand-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(base),'checks':len(report['checks']),'errors':report['errors'],'external_requests':report['external_requests']},ensure_ascii=False))
raise SystemExit(bool(report['errors'] or report['external_requests']))
