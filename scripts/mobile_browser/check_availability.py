"""Disposable UI acceptance for availability, holidays and large-event reservations."""
import json,os
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR']);meta=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
assert meta['run_id']==os.environ['ROBORT_MOBILE_RUN_ID']
origin=meta['origin']
report={'checks':[],'errors':[],'external_requests':[],'failures':[]}
def check(name,ok):
    report['checks'].append({'name':name,'passed':bool(ok)})
    if not ok:report['failures'].append(name)
def width(page,name):check(name+' width',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
def load(page,path):
    page.goto(origin+path);page.wait_for_load_state('networkidle')
def submit(page,form):
    page.locator(form+' [type=submit]').click();page.wait_for_load_state('networkidle')
def route_handler(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()

try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE') or r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
        for index,(w,h) in enumerate(((320,740),(390,844),(768,1024),(1280,900))):
            context=browser.new_context(viewport={'width':w,'height':h},is_mobile=w<1000,has_touch=w<1000)
            context.route('**/*',route_handler)
            page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
            load(page,'/admin');page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台').click();page.locator('#main h1').wait_for()
            for name in ('occupancies','holidays','sessions','calendar'):
                load(page,'/admin#'+name);page.locator('#main h1').wait_for();check(f'{w} {name} loaded',not page.locator('.error-block').count());width(page,f'{w} {name}')
            load(page,'/admin#occupancies');page.locator('[data-action=occupancy-new]').click();page.locator('#occupancy-form').wait_for()
            date=f'2026-10-{20+index:02}'
            page.locator('#f-title').fill(f'手机测试训练{w}');page.locator('#f-date').fill(date);page.locator('#f-start').fill('17:30');page.locator('#f-end').fill('19:00');width(page,f'{w} occupancy modal');submit(page,'#occupancy-form');page.locator('#modal').wait_for(state='hidden')
            page.locator('#occupancy-list').get_by_text(f'手机测试训练{w}',exact=True).wait_for()
            check(f'{w} saved occupancy',page.locator('#occupancy-list').get_by_text(f'手机测试训练{w}',exact=True).count()==1)
            load(page,'/admin#calendar');page.locator('#calendar-month').fill('2026-10');page.locator('#calendar-month').dispatch_event('change');page.locator(f'[data-date="{date}"]').click();page.locator('#calendar-detail').get_by_text('已占用 · 不可报名',exact=True).wait_for();width(page,f'{w} blocked calendar')
            page.screenshot(path=str(base/f'occupancy-{w}.png'),full_page=True)
            load(page,'/admin#occupancies');row=page.locator('#occupancy-list tr').filter(has_text=f'手机测试训练{w}');row.locator('[data-action=occupancy-release]').click();submit(page,'#release-occupancy-form');page.locator('#modal').wait_for(state='hidden');page.locator('#occupancy-list').get_by_text(f'手机测试训练{w}',exact=True).wait_for(state='hidden');check(f'{w} released',page.locator('#occupancy-list').get_by_text(f'手机测试训练{w}',exact=True).count()==0)
            load(page,'/admin#holidays');page.locator('#holiday-year').select_option('2027');page.wait_for_load_state('networkidle');page.locator('.holiday-period summary').first.click();page.locator('.holiday-period-form [name=enabled]').check();page.locator('.holiday-period-form [name=name]').first.fill('假期训练'+str(w));width(page,f'{w} holiday editing');submit(page,'#holiday-period-form-0');page.get_by_text('本假期安排已保存，报名日历已同步。',exact=True).wait_for();page.locator('.holiday-period summary').first.wait_for();page.locator('.holiday-period summary').first.click();check(f'{w} holiday saved',page.locator('.holiday-period-form [name=enabled]').is_checked());page.screenshot(path=str(base/f'holiday-{w}.png'),full_page=True)
            load(page,'/admin#tournaments');page.locator('[data-action=tournament-new]').click();page.locator('#tournament-form').wait_for();check(f'{w} venue first',page.locator('#tournament-form input,#tournament-form select').first.get_attribute('name')=='event_venue_id');page.locator('#f-title').fill(f'场地预留杯{w}');page.locator('#f-keyword').fill(f'预留杯{w}');page.locator('#f-date').fill(f'2026-11-{index+2:02}');page.locator('#f-date').dispatch_event('change');page.locator('#f-reserve_start').fill('10:00');page.locator('#f-reserve_end').fill('18:00');width(page,f'{w} event modal');page.locator('#tournament-form [type=submit]').wait_for(state='visible');submit(page,'#tournament-form');page.locator('#modal').wait_for(state='hidden');page.locator('[data-tab=organization]').click();page.locator('[data-action=event-reservations]').wait_for();check(f'{w} event reservation visible',page.locator('#main').get_by_text('10:00–18:00',exact=False).count()>0);page.locator('[data-action=event-reservations]').click();page.locator('#event-reservations-form').wait_for();width(page,f'{w} event reservation modal');check(f'{w} saved reservation',page.locator('.reservation-row').count()==1);page.locator('[data-action=modal-close]').first.click()
            context.close()
        browser.close()
except Exception as error:
    report['errors'].append(str(error))
finally:
    (base/'availability-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'errors':report['errors'],'external_requests':len(report['external_requests']),'output':str(base)},ensure_ascii=False))
raise SystemExit(bool(report['failures'] or report['errors'] or report['external_requests']))
