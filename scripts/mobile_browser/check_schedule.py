"""Browser workflows for periodic, one-off, holiday plans and calendar marks."""
import json, os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR']);meta=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
origin=meta['origin'];report={'checks':[],'failures':[],'errors':[],'external_requests':[]}
def check(name,ok):
    report['checks'].append({'name':name,'passed':bool(ok)})
    if not ok:
        report['failures'].append(name)
        report.setdefault('debug',[]).append({'name':name,'heading':page.locator('.activity-day-heading').all_inner_texts(),
            'sessions':page.locator('.activity-session-name strong').all_inner_texts(),
            'periods':page.locator('.holiday-period summary').all_inner_texts(),
            'errors':page.locator('.inline-error,.toasts').all_inner_texts()})
def width(page,name):check(name+' width',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
def load(page,route):
    page.goto(origin+'/admin#'+route);page.wait_for_load_state('networkidle');page.locator('#main h1').wait_for()
def submit(page,form):
    page.locator(form+' [type=submit]').click();page.wait_for_load_state('networkidle')
def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        widths=((320,740),) if os.environ.get('ROBORT_SCHEDULE_DEBUG') else ((320,740),(390,844),(844,390),(1280,900))
        for index,(w,h) in enumerate(widths):
            context=browser.new_context(viewport={'width':w,'height':h},is_mobile=w<1000,has_touch=w<1000);context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)));page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#calendar');page.wait_for_load_state('networkidle');page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台',exact=True).click();page.locator('#activity-date').wait_for()
            cell=page.locator('.calendar-day[data-date="2026-10-01"]');check(f'{w} holiday mark',cell.locator('.calendar-day-flag').inner_text()=='节')
            check(f'{w} makeup mark',page.locator('.calendar-day[data-date="2026-10-10"] .calendar-day-flag').inner_text()=='班')
            check(f'{w} weekend mark',page.locator('.calendar-day[data-date="2026-10-17"] .calendar-day-flag').inner_text()=='休')
            check(f'{w} enrollment visual',int(page.locator('.calendar-day[data-date="2026-10-14"] .calendar-enrollment strong').inner_text())>=10)
            check(f'{w} segmented bar',page.locator('.calendar-day[data-date="2026-10-14"] .calendar-load .formal').get_attribute('style')!='width:0%')
            check(f'{w} competition mark',page.locator('.calendar-day[data-date="2026-10-18"] .calendar-event-mark').count()==1)
            page.locator('.calendar-day[data-date="2026-10-18"]').click();page.wait_for_load_state('networkidle');check(f'{w} competition detail',bool(page.locator('.calendar-event-list').inner_text().strip()))
            width(page,f'{w} visual calendar');page.evaluate('document.activeElement.blur();window.scrollTo(0,0)');page.screenshot(path=str(base/f'schedule-calendar-{w}.png'),full_page=True)
            load(page,'sessions');check(f'{w} split schedule tabs',page.get_by_role('tab',name='周期性时段',exact=True).count()==1 and page.get_by_role('tab',name='非周期性时段',exact=True).count()==1)
            page.locator('[data-action=activity-slot-edit]').first.click();page.locator('[name=skip_holidays]').check();submit(page,'#activity-slot-form');page.locator('#modal').wait_for(state='hidden');page.locator('[data-action=activity-slot-edit]').first.wait_for();check(f'{w} saved skip flag','跳过节假日' in page.locator('.activity-rule').first.inner_text())
            page.get_by_role('tab',name='非周期性时段',exact=True).click();page.locator('[data-action=date-plan-new]').first.click();page.locator('#date-plan-form').wait_for();page.wait_for_load_state('networkidle')
            day=f'2026-11-{index+12:02}';page.locator('#f-plan_date').fill(day);page.locator('#f-plan_date').dispatch_event('change');page.wait_for_load_state('networkidle');page.locator('#date-plan-rows [name=name]').first.fill('临时活动'+str(w));page.locator('#date-plan-rows [name=start]').first.fill('17:00');width(page,f'{w} nonrecurring dialog');submit(page,'#date-plan-form');page.locator('#modal').wait_for(state='hidden');page.locator('.date-plan-card').filter(has_text=day).wait_for();check(f'{w} one-off list', '临时活动'+str(w) in page.locator('.date-plan-card').filter(has_text=day).inner_text());width(page,f'{w} one-off list')
            load(page,'calendar');page.locator('#activity-date').fill(day);page.locator('#activity-date').dispatch_event('change');page.locator('.activity-day-heading').filter(has_text=day).wait_for();page.locator('.activity-session-name strong').filter(has_text='临时活动'+str(w)).wait_for();check(f'{w} one-off calendar synced','临时活动'+str(w) in page.locator('#calendar-detail').inner_text())
            load(page,'sessions');page.get_by_role('tab',name='非周期性时段',exact=True).click();page.locator('.date-plan-card').filter(has_text=day).locator('[data-action=date-plan-remove]').click();page.get_by_role('button',name='恢复安排',exact=True).click();page.locator('#modal').wait_for(state='hidden');page.wait_for_load_state('networkidle');check(f'{w} restore removed override',page.locator('.date-plan-card').filter(has_text=day).count()==0)
            load(page,'holidays');check(f'{w} annual seven holidays',page.locator('.holiday-period').count()==7);page.locator('.holiday-period summary').first.click();check(f'{w} expired holiday read only',page.locator('.holiday-period-form').first.locator('fieldset').get_attribute('disabled') is not None);width(page,f'{w} holiday list')
            page.locator('#holiday-year').select_option('2027');page.wait_for_load_state('networkidle');page.locator('.holiday-period summary').first.click();form=page.locator('.holiday-period-form').first;form.locator('[name=enabled]').check();form.locator('[name=end_date]').fill('2027-01-02');form.locator('[name=name]').first.fill('新年专用场'+str(w));form.locator('[name=start]').first.fill('12:00');form.locator('[name=end]').first.fill('14:00');width(page,f'{w} holiday editing');submit(page,'#holiday-period-form-0');page.locator('.holiday-period summary').filter(has_text='2027-01-02').wait_for();page.wait_for_load_state('networkidle');check(f'{w} separate holiday saved','2027-01-02' in page.locator('.holiday-period summary').inner_text());page.locator('.holiday-period summary').first.click();page.evaluate('document.activeElement.blur();window.scrollTo(0,0)');page.screenshot(path=str(base/f'schedule-holidays-{w}.png'),full_page=True)
            load(page,'calendar');page.locator('#activity-date').fill('2027-01-02');page.locator('#activity-date').dispatch_event('change');page.locator('.activity-day-heading').filter(has_text='2027-01-02').wait_for();page.locator('.activity-session-name strong').filter(has_text='新年专用场'+str(w)).wait_for();check(f'{w} holiday calendar sync','新年专用场'+str(w) in page.locator('#calendar-detail').inner_text());check(f'{w} edited holiday range marked',page.locator('.calendar-day[data-date="2027-01-02"] .calendar-day-flag').inner_text()=='节');width(page,f'{w} future holiday calendar')
            context.close()
        browser.close()
except Exception as error:report['errors'].append(str(error))
finally:(base/'schedule-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'errors':report['errors'],'external_requests':len(report['external_requests']),'output':str(base)},ensure_ascii=False))
raise SystemExit(bool(report['failures'] or report['errors'] or report['external_requests']))
