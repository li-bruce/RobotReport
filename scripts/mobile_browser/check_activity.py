"""Real browser acceptance: multi-venue planning, lazy rosters and historic locks."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR']);meta=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
origin=meta['origin'];report={'checks':[],'errors':[],'failures':[],'external_requests':[]}
def check(name,ok):
    report['checks'].append({'name':name,'passed':bool(ok)})
    if not ok:report['failures'].append(name)
def width(page,name):check(name+' no page overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
def load(page,route):
    page.goto(origin+'/admin#'+route);page.wait_for_load_state('networkidle');page.locator('#main h1').wait_for()
def submit(page,form):
    page.locator(form+' [type=submit]').click();page.wait_for_load_state('networkidle');page.locator('#modal').wait_for(state='hidden')
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        for w,h in ((320,740),(390,844),(844,390),(1280,900)):
            context=browser.new_context(viewport={'width':w,'height':h},is_mobile=w<1000,has_touch=w<1000)
            def intercept(route):
                if route.request.url.startswith(origin+'/'):route.continue_()
                else:report['external_requests'].append(route.request.url);route.abort()
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#calendar');page.wait_for_load_state('networkidle');page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台',exact=True).click();page.locator('#activity-date').wait_for()
            check(f'{w} no global venue picker',page.locator('.topbar select').count()==0)
            check(f'{w} activity nav has named calendar',page.locator('.activity-nav-groups a').filter(has_text='报名日历').count()==1)
            page.locator('#activity-date').fill('2026-10-16');page.locator('#activity-date').dispatch_event('change');page.wait_for_load_state('networkidle')
            check(f'{w} dense day count', '42 场' in page.locator('.activity-day-heading').inner_text())
            check(f'{w} bounded cards',page.locator('.activity-session').count()==12)
            check(f'{w} no roster before expansion',page.locator('#calendar-detail table').count()==0)
            width(page,f'{w} dense calendar')
            second=str(meta['activity_second']);page.locator('#day-venue').select_option(second)
            check(f'{w} venue filter',page.locator('.activity-venue').count()==1)
            page.locator('.activity-session summary').first.click();page.locator('.activity-roster[data-ready], .activity-roster table, .activity-roster .empty').first.wait_for();page.wait_for_load_state('networkidle')
            check(f'{w} one roster request',page.locator('.activity-session[open]').count()==1)
            page.locator('[data-action=activity-page][data-delta="1"]').click()
            check(f'{w} next page remaining sessions',page.locator('.activity-session').count()==8)
            page.locator('#day-venue').select_option('1')
            page.locator('.activity-session summary').first.click();page.locator('#calendar-detail table').wait_for()
            check(f'{w} primary roster isolated', '张三' in page.locator('#calendar-detail table').inner_text())
            check(f'{w} actionable future',page.locator('#calendar-detail [data-action=registration-cancel]').count()>0)
            width(page,f'{w} future roster')
            page.evaluate('document.activeElement.blur(); window.scrollTo(0,0)');page.screenshot(path=str(base/f'activity-calendar-{w}.png'),full_page=True)
            page.locator('#activity-date').fill('2026-10-13');page.locator('#activity-date').dispatch_event('change');page.wait_for_load_state('networkidle')
            first=page.locator('.activity-session').first
            if not first.get_attribute('open'):first.locator('summary').click()
            page.locator('#calendar-detail table').wait_for()
            check(f'{w} historical cancel hidden',page.locator('#calendar-detail [data-action=registration-cancel]').count()==0)
            check(f'{w} historical editing hidden',page.locator('#calendar-detail [data-action=registration-new],#calendar-detail [data-action=session-constraints]').count()==0)
            page.evaluate('document.activeElement.blur(); window.scrollTo(0,0)');page.screenshot(path=str(base/f'activity-history-{w}.png'),full_page=True)
            load(page,'sessions');page.locator('[data-action=activity-slot-new]').click();page.locator('#activity-slot-form').wait_for()
            page.locator('#activity-slot-form [name=name]').fill('夜间练习'+str(w));page.locator('#activity-slot-form [name=start]').fill('22:00');page.locator('#activity-slot-form [name=end]').fill('23:00')
            page.locator(f'#activity-slot-form [name=venue_ids][value="{second}"]').check()
            width(page,f'{w} slot form');submit(page,'#activity-slot-form')
            card=page.locator('.activity-rule').filter(has_text='夜间练习'+str(w));card.wait_for()
            check(f'{w} multi venue rule saved',card.locator('.activity-rule-venues .badge').count()==2)
            card.locator('[data-action=activity-slot-edit]').click();page.locator('#activity-slot-form [name=start]').fill('22:15');submit(page,'#activity-slot-form')
            load(page,'calendar');page.locator('#activity-date').fill('2026-10-16');page.locator('#activity-date').dispatch_event('change');page.wait_for_load_state('networkidle')
            page.locator('#day-venue').select_option('');page.locator('#day-search').fill('夜间练习'+str(w));page.wait_for_load_state('networkidle')
            check(f'{w} edited schedule immediately in both venues',page.locator('.activity-session').count()==2)
            check(f'{w} new time shown',all('22:15–23:00' in x.inner_text() for x in page.locator('.activity-session').all()))
            page.evaluate('document.activeElement.blur(); window.scrollTo(0,0)');page.screenshot(path=str(base/f'activity-multivenue-{w}.png'),full_page=True)
            load(page,'sessions');page.locator('.activity-rule').filter(has_text='夜间练习'+str(w)).locator('[data-action=activity-slot-delete]').click();page.get_by_role('button',name='移除时段',exact=True).click();page.locator('#modal').wait_for(state='hidden')
            load(page,'venues');page.locator('[data-action=venue-edit]').first.click();page.locator('#f-description').fill('浏览器验收场地说明');submit(page,'#venue-form');page.locator('#main').get_by_text('浏览器验收场地说明',exact=True).wait_for();check(f'{w} edit venue details', '浏览器验收场地说明' in page.locator('#main').inner_text())
            for route in ('occupancies','holidays','daily','casual','stats','tournaments','dashboard'):
                load(page,route);page.locator('#page-venue').wait_for();check(f'{w} {route} local venue',page.locator('#main #page-venue').count()==1);width(page,f'{w} {route}')
            load(page,'reminders');check(f'{w} reminders under settings',page.locator('.nav a.active').get_attribute('data-route')=='settings');width(page,f'{w} reminders')
            context.close()
        browser.close()
except Exception as error:
    report['errors'].append(str(error))
finally:
    (base/'activity-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'errors':report['errors'],'external_requests':len(report['external_requests']),'output':str(base)},ensure_ascii=False))
raise SystemExit(bool(report['failures'] or report['errors'] or report['external_requests']))
