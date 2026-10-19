"""Real forms at phone/desktop widths; isolated fixture, no external traffic."""
import io
import json
import os
from datetime import datetime
from pathlib import Path
from openpyxl import Workbook
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
origin=meta['origin']
report={'checks':[],'failures':[],'errors':[],'external_requests':[]}
def check(name,value):
    report['checks'].append({'name':name,'passed':bool(value)})
    if not value:report['failures'].append(name)
def fit(page,name):check(name,page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        for width,height in ((320,740),(390,844),(844,390),(1280,900)):
            context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<1000,has_touch=width<1000)
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#members');page.wait_for_load_state('networkidle')
            page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('[data-action=member-new]').wait_for();page.locator('[data-action=member-new]').click()
            page.locator('#member-form').wait_for();check(f'{width} admin form has no account ID',page.locator('#member-form [name=user_id]').count()==0)
            page.locator('#member-form [name=name]').fill('人工登记'+str(width));page.locator('#member-form [type=submit]').click()
            page.locator('#modal').wait_for(state='hidden')
            page.goto(origin+'/admin#member-imports');page.locator('#member-import-file').wait_for()
            check(f'{width} generated preregistration', '人工登记'+str(width) in page.locator('#member-import-records').inner_text())
            book=Workbook();sheet=book.active;sheet.title='名单';sheet.append(['俱乐部会员']);sheet.append(['人员姓名','联系手机','备注']);sheet.append(['导入新人'+str(width),'1390013'+str(width).zfill(4),'忽略备注']);sheet.append(['无手机号'+str(width),None,'待补'])
            stream=io.BytesIO();book.save(stream);book.close()
            page.locator('#member-import-file').set_input_files({'name':'名单.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':stream.getvalue()})
            page.locator('#member-import-upload [type=submit]').click();page.locator('#member-import-commit').wait_for()
            check(f'{width} Excel mapping and missing fields', '已识别的列' in page.locator('#member-import-preview').inner_text() and '待补全' in page.locator('#member-import-preview').inner_text())
            fit(page,f'{width} import preview fits');page.screenshot(path=str(base/f'import-preview-{width}.png'),full_page=True)
            page.locator('#member-import-commit [type=submit]').click();page.locator('.import-complete').wait_for()
            record=page.locator('#member-import-records tr').filter(has_text='无手机号'+str(width));record.get_by_role('button',name='补全资料',exact=True).click()
            page.locator('#prereg-edit [name=phone]').fill('1370013'+str(width).zfill(4));page.locator('#prereg-edit [type=submit]').click();page.locator('#modal').wait_for(state='hidden')
            page.goto(meta['forms'][str(width)]);page.wait_for_load_state('networkidle');page.locator('#registration:not([hidden])').wait_for()
            check(f'{width} public form no ID',page.locator('[name=user_id]').count()==0)
            page.locator('[name=name]').fill('自行填写'+str(width));page.locator('[name=gender]').select_option('female');page.locator('[name=level]').select_option('5');page.locator('[name=phone]').fill('1390013'+str(width).zfill(4))
            page.locator('#registration [type=submit]').click();page.get_by_role('heading',name='申请已提交',exact=True).wait_for()
            check(f'{width} incomplete import waits approval','待' in page.locator('#result-message').inner_text());fit(page,f'{width} registration result fits')
            page.goto(origin+'/admin#reminders');page.locator('#broadcast-rules').wait_for()
            page.locator('#broadcast-rules [data-add-rule]').click();rule=page.locator('[data-time-rule]').last
            rule.locator('[data-rule-field=name]').fill('周末播报'+str(width));rule.locator('[data-rule-field=time]').fill('12:30')
            rule.locator('[data-rule-choice=kinds][value=roster]').check();rule.locator('.time-conditions>summary').click()
            rule.locator('[data-rule-field=day_type]').select_option('weekend');rule.locator('[data-rule-field=holidays]').select_option('exclude')
            rule.locator('[data-rule-field=start_date]').fill('2026-10-20');rule.locator('[data-rule-field=end_date]').fill('2026-11-20')
            fit(page,f'{width} broadcast editor fits');page.screenshot(path=str(base/f'broadcast-editor-{width}.png'),full_page=True)
            with page.expect_response(lambda r:r.request.method=='PATCH' and r.url.endswith('/api/config')) as saved:page.locator('#reminder-form [type=submit]').click()
            check(f'{width} broadcast saved',saved.value.status==200)
            page.locator('#broadcast-rules').get_by_text('周末播报'+str(width),exact=True).wait_for()
            page.goto(origin+'/admin#bots');card=page.locator('.robot-card').first;card.wait_for();card.locator(':scope>summary').click();card.locator('[data-action=bot-edit]').click();page.locator('#bot-form').wait_for()
            page.locator('#bot-form summary').filter(has_text='功能开放时间').click();page.locator('#bot-time-rules [data-add-rule]').click();rule=page.locator('#bot-time-rules [data-time-rule]').last
            rule.locator('[data-rule-field=name]').fill('报名晚间开放'+str(width));rule.locator('[data-rule-choice=capabilities][value=registration]').uncheck();rule.locator('[data-rule-choice=capabilities][value=enrollment]').check()
            rule.locator('.time-conditions>summary').click();rule.locator('[data-rule-field=start_time]').fill('17:00');rule.locator('[data-rule-field=end_time]').fill('22:00')
            fit(page,f'{width} bot time rules fit');page.screenshot(path=str(base/f'bot-time-rules-{width}.png'),full_page=True)
            with page.expect_response(lambda r:r.request.method=='PATCH' and '/api/bots/' in r.url) as saved:page.locator('#bot-form [type=submit]').click()
            check(f'{width} bot time rules saved',saved.value.status==200);page.locator('#modal').wait_for(state='hidden')
            context.close()
        browser.close()
except Exception as error:report['errors'].append(str(error))
finally:
    (base/'operations-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'errors':report['errors'],'external_requests':len(report['external_requests']),'output':str(base)},ensure_ascii=False))
raise SystemExit(bool(report['failures'] or report['errors'] or report['external_requests']))
