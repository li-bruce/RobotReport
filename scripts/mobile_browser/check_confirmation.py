"""Exercise simplified confirmation and imported-account reconciliation in a browser."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
origin=meta['origin'].replace('127.0.0.1','robort.test')
report={'checks':[],'failures':[],'errors':[],'external_requests':[]}
reject_stale_preview=True


def check(name,passed):
    report['checks'].append({'name':name,'passed':bool(passed)})
    if not passed:report['failures'].append(name)


def fit(page,label):
    check(label+' fits',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))


def intercept(route):
    global reject_stale_preview
    if not route.request.url.startswith(origin+'/'):
        report['external_requests'].append(route.request.url);route.abort();return
    if reject_stale_preview and route.request.url.endswith('/api/member-imports/invite') and route.request.method=='POST':
        reject_stale_preview=False
        route.fulfill(status=400,json={'detail':'名单已变化，请重新预览后发送'});return
    route.continue_()


try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True,
                                  args=['--host-resolver-rules=MAP robort.test 127.0.0.1','--no-proxy-server'])
        for index,(width,height) in enumerate(((320,740),(390,844),(844,390),(1280,900))):
            label=str(width)
            context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<1000,has_touch=width<1000)
            context.route('**/*',intercept)
            page=context.new_page()
            page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#member-imports')
            check(label+' insecure HTTP without randomUUID',page.evaluate("!isSecureContext && typeof crypto.randomUUID==='undefined'"))
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('#member-import-records').wait_for()
            target=meta['confirmation'][label]
            row=page.locator('#member-import-records tr').filter(has_text=target['name'])
            row.locator('[data-import-check]').check()
            page.get_by_role('button',name='通知本人确认',exact=True).click()
            page.locator('#prereg-invite').wait_for()
            page.get_by_role('button',name='预览通知',exact=True).click()
            page.get_by_role('button',name='发送确认通知',exact=True).wait_for()
            check(label+' invitation concise',target['name'] in page.locator('#invite-preview').inner_text() and '注册确认' in page.locator('#invite-preview').inner_text())
            fit(page,label+' invite')
            page.get_by_role('button',name='发送确认通知',exact=True).click()
            if index==0:
                page.get_by_role('button',name='预览通知',exact=True).wait_for()
                check('stale preview returns to preview','名单已变化' in page.locator('#prereg-invite .inline-error').inner_text())
                check('stale preview is cleared',not page.locator('#invite-preview').inner_text())
                page.get_by_role('button',name='预览通知',exact=True).click()
                page.get_by_role('button',name='发送确认通知',exact=True).click()
            page.locator('#modal').wait_for(state='hidden')
            existing=page.locator('#member-import-records tr').filter(has_text=target['existing_name'])
            existing.get_by_role('button',name='补全资料',exact=True).click()
            page.locator('#prereg-edit [name=phone]').fill(target['existing_phone'])
            page.locator('#prereg-edit [type=submit]').click()
            page.locator('#modal').wait_for(state='hidden')
            page.wait_for_load_state('networkidle')
            check(label+' existing account linked', '已关联' in existing.inner_text())
            fit(page,label+' import')
            page.screenshot(path=str(base/f'confirmation-admin-{label}.png'),full_page=True)
            page.goto(meta['forms'][label].replace(meta['origin'],origin))
            page.locator('#registration').wait_for(state='visible')
            with page.expect_response(lambda r:r.url.endswith('/lookup')) as lookup:
                page.locator('[name=name]').fill(target['name'])
                page.locator('[name=name]').press('Tab')
            assert lookup.value.ok,lookup.value.text()
            page.wait_for_function("document.querySelector('[name=gender]').value==='male'")
            check(label+' no phone required',not page.locator('[name=phone]').get_attribute('required'))
            check(label+' no internal id fields',page.locator('input[name=user_id]').count()==0)
            check(label+' imported gender prefilled',page.locator('[name=gender]').input_value()=='male')
            fit(page,label+' personal')
            page.screenshot(path=str(base/f'confirmation-form-{label}.png'),full_page=True)
            page.locator('#registration [type=submit]').click()
            page.get_by_role('heading',name='资料已关联，注册成功',exact=True).wait_for()
            check(label+' direct approval','审核' not in page.locator('#result-message').inner_text())
            page.reload()
            page.get_by_role('heading',name='资料已关联，注册成功',exact=True).wait_for()
            check(label+' repeat submission stable',page.locator('#registration').is_hidden())
            if index==3:
                page.goto(origin+'/admin#member-imports')
                page.get_by_role('button',name='匹配已有会员',exact=True).click()
                page.locator('#prereg-reconcile').wait_for()
                check('bulk preview', '可关联' in page.locator('#modal').inner_text())
                page.locator('#prereg-reconcile [type=submit]').click()
                page.locator('#modal').wait_for(state='hidden')
                page.wait_for_load_state('networkidle')
                check('bulk account linked','已关联' in page.locator('#member-import-records tr').filter(has_text=meta['confirmation_bulk']).inner_text())
            context.close()
        browser.close()
except Exception as error:
    report['errors'].append(str(error))
finally:
    (base/'confirmation-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'errors':report['errors'],'external_requests':len(report['external_requests'])},ensure_ascii=False))
raise SystemExit(bool(report['failures'] or report['errors'] or report['external_requests']))
