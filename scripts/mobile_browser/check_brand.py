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
        for width in (320,390,768,1280):
            context=browser.new_context(viewport={'width':width,'height':900})
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#dashboard')
            check('login product name','聚场' in page.title())
            fit(page,f'{width} login fits')
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('.gather-hero').wait_for()
            page.wait_for_function("document.querySelector('.gather-scene img').complete && document.querySelector('.gather-scene img').naturalWidth===1536")
            check('concept modules',page.locator('.gather-domains a').count()==4)
            check('competition boundary visible','当前支持羽毛球' in page.locator('.gather-domains').inner_text())
            check('workspace is generic','聚场管理后台' in page.locator('.workspace-label').inner_text())
            fit(page,f'{width} homepage fits')
            page.screenshot(path=str(base/f'homepage-{width}.png'),full_page=True)
            page.locator('.gather-intro [data-route=bots]').click()
            card=page.locator('.robot-card').first
            card.locator(':scope>summary').click()
            selected=card.get_attribute('data-bot-id')
            other=next(bot for bot in meta['bot_ids'] if bot!=selected)
            card.locator('[data-action=bot-reminders]').click()
            page.locator('#broadcast-bot-filter').wait_for()
            check('shortcut selects robot',page.locator('#broadcast-bot-filter').input_value()==selected)
            check('broadcast belongs to bots',page.locator('.nav a.active').get_attribute('data-route')=='bots')
            check('broadcast tab active',page.locator('.section-tabs a.active').inner_text()=='定时播报')
            fit(page,f'{width} schedules fit')
            first_name=f'一号规则{width}'
            new_rule(page,first_name,'11:00',selected)
            save(page)
            before=config(context);first=next(r for r in before['broadcast_rules'] if r['name']==first_name)
            page.locator('#broadcast-bot-filter').select_option(other)
            check('other robot hides first rule',not page.locator(f'[data-time-rule="{first["id"]}"]').is_visible())
            new_rule(page,f'二号规则{width}','12:00',other)
            save(page)
            after=config(context)
            check('saving filtered rules preserves every existing rule',all(r in after['broadcast_rules'] for r in before['broadcast_rules']))
            check('global switch preserved',before['daily_enabled']==after['daily_enabled'])
            second=next(r for r in after['broadcast_rules'] if r['name']==f'二号规则{width}')
            page.locator('#broadcast-bot-filter').select_option(selected)
            rule=page.locator(f'[data-time-rule="{first["id"]}"]')
            rule.locator(':scope>summary').click()
            rule.locator('[data-rule-field=time]').fill('11:05')
            save(page)
            changed=config(context)
            check('edit leaves other robot unchanged',second in changed['broadcast_rules'])
            check('edited selected rule saved',next(r for r in changed['broadcast_rules'] if r['id']==first['id'])['time']=='11:05')
            page.screenshot(path=str(base/f'broadcasts-{width}.png'),full_page=True)
            page.goto(origin+'/admin#settings');page.locator('#main .loading').wait_for(state='hidden');page.wait_for_load_state('networkidle')
            check('system tabs omit broadcast',page.locator('.section-tabs [data-route=reminders]').count()==0)
            context.close()
        browser.close()
except Exception as error:report['errors'].append(str(error))
finally:
    (base/'brand-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(base),'checks':len(report['checks']),'errors':report['errors'],'external_requests':report['external_requests']},ensure_ascii=False))
raise SystemExit(bool(report['errors'] or report['external_requests']))
