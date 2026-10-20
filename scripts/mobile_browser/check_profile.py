"""Isolated personal form -> administrator approval on phone and desktop."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base = Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta = json.loads((base/'fixture.json').read_text(encoding='utf-8'))
origin = meta['origin']
report = {'checks': [], 'failures': [], 'errors': [], 'external_requests': []}


def check(name, passed):
    report['checks'].append({'name': name, 'passed': bool(passed)})
    if not passed:
        report['failures'].append(name)


def intercept(route):
    if route.request.url.startswith(origin+'/'):
        route.continue_()
    else:
        report['external_requests'].append(route.request.url)
        route.abort()


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'), headless=True)
        for width, height in ((320,740),(390,844),(844,390),(1280,900)):
            context = browser.new_context(viewport={'width':width,'height':height}, is_mobile=width<1000, has_touch=width<1000)
            context.route('**/*', intercept)
            page = context.new_page()
            page.on('pageerror', lambda error: report['errors'].append(str(error)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(meta['profile_forms'][str(width)])
            page.get_by_role('heading', name='申请调整资料').wait_for()
            check(f'{width} form bound to member', bool(page.locator('[name=name]').input_value()))
            check(f'{width} no full phone prefilled', not page.locator('[name=phone]').input_value())
            check(f'{width} profile groups visible', page.locator('#profile-groups input').count()>0)
            check(f'{width} form fits screen', page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
            page.locator('[name=level]').select_option('8')
            page.screenshot(path=str(base/f'profile-form-{width}.png'), full_page=True)
            with page.expect_response(lambda r:r.request.method=='POST' and '/public/registration/' in r.url) as submission:
                page.get_by_role('button', name='提交资料申请', exact=True).click()
            page.get_by_role('heading', name='资料申请已提交').wait_for()
            check(f'{width} pending notice', '等待管理员审核' in page.locator('#result-message').inner_text())
            request_id = str(submission.value.json()['request_id'])
            page.goto(origin+'/admin#profile-requests')
            page.get_by_label('管理员密码', exact=True).fill(meta['password'])
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.locator('#member-status-filter').wait_for()
            page.locator(f'[data-action=profile-review][data-id="{request_id}"]').wait_for()
            check(f'{width} review navigation', page.locator('.section-tabs a[href="#profile-requests"]').count()==0)
            check(f'{width} list fits screen', page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
            page.screenshot(path=str(base/f'profile-admin-{width}.png'), full_page=True)
            page.locator(f'[data-action=profile-review][data-id="{request_id}"]').click()
            page.locator('#profile-review-form').wait_for()
            check(f'{width} original and proposed values', '8级' in page.locator('#modal').inner_text() and '原资料' in page.locator('#modal').inner_text())
            check(f'{width} dialog fits screen', page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
            page.locator('#profile-review-form [type=submit]').click()
            page.locator('#modal').wait_for(state='hidden')
            page.wait_for_load_state('networkidle')
            check(f'{width} removed from pending', not page.locator(f'[data-action=profile-review][data-id="{request_id}"]').count())
            page.locator('#member-status-filter').select_option('')
            page.locator(f'[data-action=member-edit][data-user-id="{meta["users"][list((320,390,844,1280)).index(width)+3]}"]').click()
            page.locator('#member-form').wait_for()
            check(f'{width} approved history retained', '资料调整 · 已通过' in page.locator('#modal').inner_text())
            context.close()
        browser.close()
except Exception as error:
    report['errors'].append(str(error))
finally:
    (base/'profile-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'errors':report['errors'],'external_requests':len(report['external_requests'])},ensure_ascii=False))
raise SystemExit(bool(report['failures'] or report['errors'] or report['external_requests']))
