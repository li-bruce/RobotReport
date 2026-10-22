"""Inline robot edits at narrow/wide widths using isolated synthetic records."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta=json.loads((base/'fixture.json').read_text(encoding='utf8'))
origin=meta['origin'];report={'checks':[],'errors':[],'external_requests':[]}
def check(name,value):
    report['checks'].append({'name':name,'passed':bool(value)})
    assert value,name
def fit(page,name):check(name,page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()
def save(page,form,suffix,method):
    with page.expect_response(lambda r:r.request.method==method and r.url.endswith(suffix)) as response:
        form.locator('[type=submit]').click()
    check('save succeeds',response.value.status==200)
    form.locator('[data-save-status]').filter(has_text='已保存').wait_for()
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        for width,height in ((320,740),(390,844),(768,1024),(844,390),(1280,900),(1920,1080)):
            context=browser.new_context(viewport={'width':width,'height':height})
            context.route('**/*',intercept);page=context.new_page()
            page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.goto(origin+'/admin#bots')
            page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台',exact=True).click()
            cards=page.locator('.robot-manager>.robot-card');cards.first.wait_for()
            check('only two workspace tabs',page.locator('.section-tabs a').count()==2)
            check('separate activity and reminder times','场次' in cards.first.inner_text() and '播报' in cards.first.inner_text())
            fit(page,f'{width} collapsed fits');page.screenshot(path=str(base/f'robot-list-{width}.png'),full_page=True)
            first,second=cards.nth(0),cards.nth(1)
            id1,id2=first.get_attribute('data-bot-id'),second.get_attribute('data-bot-id')
            first.locator(':scope>summary').click();first.locator('.bot-inline-form').wait_for()
            second.locator(':scope>summary').click();second.locator('.bot-inline-form').wait_for()
            check('no modal or route change',page.url.endswith('#bots') and not page.locator('#modal').is_visible())
            check('five settings panels',first.locator('.bot-setting-panel').count()==5)
            check('unique control IDs',page.evaluate('(()=>{const ids=[...document.querySelectorAll("[id]")].map(n=>n.id);return ids.length===new Set(ids).size})()'))
            f1,f2=first.locator('.bot-inline-form'),second.locator('.bot-inline-form')
            draft=f'未保存二号-{width}';f2.locator('[name=name]').fill(draft)
            first.locator(':scope>summary').click();first.locator(':scope>summary').click()
            check('collapse preserves other draft',f2.locator('[name=name]').input_value()==draft)
            f1.locator('[name=name]').fill('日常活动助手'+str(width));f1.locator('[name=slot_mode]').select_option('selected')
            f1.locator('[name=slot_keys]').first.check()
            f1.locator('[name=policy_enabled]').check();f1.locator('[data-rule-add]').click()
            # No empty policy is submitted: exercise the panel then leave conditions off.
            f1.locator('[data-rule-remove]').first.click();f1.locator('[name=policy_enabled]').uncheck()
            times=f1.locator('[data-bot-time-rules]');times.locator('[data-add-rule]').click();rule=times.locator('[data-time-rule]').last
            rule.locator('[data-rule-field=name]').fill('夜间报名'+str(width));rule.locator('[data-rule-choice=capabilities]').first.uncheck();rule.locator('[data-rule-choice=capabilities][value=enrollment]').check()
            rule.locator('.time-conditions>summary').click();rule.locator('[data-rule-field=start_time]').fill('22:00');rule.locator('[data-rule-field=end_time]').fill('02:00');rule.locator('[data-rule-choice=weekdays][value="0"]').check();rule.locator('[data-rule-field=holidays]').select_option('exclude')
            check('time summary includes weekday holiday overnight',all(t in rule.locator('summary').first.inner_text() for t in ['周一','次日','跳过节假日']))
            save(page,f1,f'/api/bots/{id1}','PATCH');check('save does not clear second draft',f2.locator('[name=name]').input_value()==draft)
            check('summary reflects saved name','日常活动助手'+str(width) in first.locator(':scope>summary').inner_text())
            b1=first.locator('[id^=robot-broadcast-]');b2=second.locator('[id^=robot-broadcast-]')
            b2.locator('[data-add-rule]').click();draft_rule=b2.locator('[data-time-rule]').last;draft_rule.locator('[data-rule-field=name]').fill('二号待保存')
            before=context.request.get(origin+f'/api/bots/{id2}/broadcasts').json()
            b1.locator('[data-add-rule]').click();rule=b1.locator('[data-time-rule]').last
            name='周末午间'+str(width);rule.locator('[data-rule-field=name]').fill(name);rule.locator('[data-rule-field=time]').fill('12:15')
            rule.locator('.time-conditions>summary').click();rule.locator('[data-rule-field=day_type]').select_option('weekend');rule.locator('[data-rule-field=start_date]').fill('2026-10-22');rule.locator('[data-rule-field=end_date]').fill('2026-12-31')
            check('broadcast has no robot picker',b1.locator('[data-rule-choice=bot_ids]').count()==0)
            fit(page,f'{width} inline forms fit');save(page,b1,f'/api/bots/{id1}/broadcasts','PUT')
            check('other robot persisted settings untouched',context.request.get(origin+f'/api/bots/{id2}/broadcasts').json()==before)
            check('other robot broadcast draft remains',draft_rule.locator('[data-rule-field=name]').input_value()=='二号待保存')
            saved=context.request.get(origin+f'/api/bots/{id1}/broadcasts').json();saved_rule=next(r for r in saved['rules'] if r['name']==name)
            check('new broadcast belongs only to owner',saved_rule['bot_ids']==[id1] and saved_rule['when']['day_type']=='weekend')
            check('collapsed broadcast times refreshed','12:15' in first.locator(':scope>summary').inner_text())
            # Saving the same open draft after another administrator edits it reports conflict in place.
            csrf=context.request.get(origin+'/auth/session').json()['csrf_token']
            external={'rules':saved['rules'],'revision':saved['revision']};external['rules'][-1]['time']='12:16'
            check('external scoped edit',context.request.put(origin+f'/api/bots/{id1}/broadcasts',data=external,headers={'x-csrf-token':csrf}).status==200)
            with page.expect_response(lambda r:r.request.method=='PUT' and r.url.endswith('/broadcasts')) as conflict:b1.locator('[type=submit]').click()
            check('stale draft denied',conflict.value.status==409);b1.locator('.inline-error').filter(has_text='重新载入').wait_for()
            check('conflict keeps entered rule',b1.locator('[data-time-rule]').filter(has_text=name).count()==1)
            # Discard only the isolated browser context, not any server data.
            second.locator(':scope>summary').click();first.scroll_into_view_if_needed()
            if width in (390,1280):page.screenshot(path=str(base/f'robot-settings-{width}.png'),full_page=True)
            context.close()
        browser.close()
except Exception as error:report['errors'].append(str(error))
finally:
    (base/'inline-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'output':str(base),'checks':len(report['checks']),'errors':report['errors'],'external_requests':report['external_requests']},ensure_ascii=False))
raise SystemExit(bool(report['errors'] or report['external_requests']))
