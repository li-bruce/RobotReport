"""Capture the actual UI for the manuals using only the disposable guide fixture."""
import io
import json
import os
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[2]
base = Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta = json.loads((base / 'fixture.json').read_text(encoding='utf-8'))
origin = meta['origin']
assets = root / 'app/static/guide-images'
assets.mkdir(exist_ok=True)
report = {'screenshots': [], 'errors': [], 'external_requests': []}


def intercept(route):
    if route.request.url.startswith(origin + '/'):
        route.continue_()
    else:
        report['external_requests'].append(route.request.url)
        route.abort()


def capture(page, name, selector=None):
    page.locator('#main .loading').wait_for(state='hidden')
    page.wait_for_load_state('networkidle')
    page.evaluate('document.fonts.ready')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
    target = page.locator(selector) if selector else page
    options = {'full_page': True} if selector is None and name.startswith('member-') else {}
    target.screenshot(path=str(assets / (name + '.png')), animations='disabled', **options)
    (base / (name + '.txt')).write_text(page.locator('body').inner_text(), encoding='utf-8')
    report['screenshots'].append(name)


def visit(page, route, ready):
    if page.locator('#modal[open]').count():
        page.keyboard.press('Escape')
        page.locator('#modal').wait_for(state='hidden')
    page.goto(origin + '/admin#' + route)
    page.reload()
    page.locator(ready).first.wait_for()
    page.locator('#main .loading').wait_for(state='hidden')
    page.wait_for_load_state('networkidle')


try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'), headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900}, device_scale_factor=1)
        context.route('**/*', intercept)
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.clock.install(time=datetime.fromisoformat(meta['clock']))
        page.goto(origin + '/admin#member-imports')
        page.get_by_label('管理员密码', exact=True).fill(meta['password'])
        page.get_by_role('button', name='登录工作台', exact=True).click()
        page.locator('#member-import-records').wait_for()
        capture(page, 'admin-imports')
        # Upload a non-template workbook and show a real duplicate candidate.
        book = Workbook()
        sheet = book.active
        sheet.title = '秋季会员'
        sheet.append(['秋季羽毛球会员登记'])
        sheet.append(['人员姓名', '性别', '联系手机', '自评等级', '备注'])
        sheet.append(['沈禾', '男', None, None, '已有预登记，需核对'])
        sheet.append(['叶青', '女', None, 3, '首次导入'])
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        page.locator('#member-import-file').set_input_files({
            'name': '秋季会员.xlsx',
            'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': stream.getvalue(),
        })
        page.locator('#member-import-upload [type=submit]').click()
        page.locator('#member-import-commit').wait_for()
        capture(page, 'admin-import-preview', '#member-import-preview')
        # Preview only; no notification is sent, even to the fixture provider.
        visit(page, 'member-imports', '#member-import-records')
        page.locator('#member-import-records tr').filter(has_text='方宁').locator('[data-import-check]').check()
        page.get_by_role('button', name='通知本人确认', exact=True).click()
        page.get_by_role('button', name='预览通知', exact=True).click()
        page.get_by_role('button', name='发送确认通知', exact=True).wait_for()
        capture(page, 'admin-invite', '#modal')

        visit(page, 'members', '#member-list')
        page.get_by_role('button', name='申请待审', exact=True).click()
        capture(page, 'admin-pending')
        page.get_by_role('button', name='审核申请', exact=True).first.click()
        capture(page, 'admin-review', '#modal')
        visit(page, 'members', '#member-list')
        page.locator('#member-status-filter').select_option('approved')
        page.set_viewport_size({'width': 1280, 'height': 1200})
        page.get_by_role('button', name='续费记录', exact=True).first.click()
        capture(page, 'admin-membership', '#modal')
        visit(page, 'roles', '#main')
        page.set_viewport_size({'width': 1280, 'height': 900})
        capture(page, 'admin-roles')
        visit(page, 'bots', '.robot-card')
        capture(page, 'admin-bots')
        visit(page, 'bot-links', '#main')
        capture(page, 'admin-bot-links')
        visit(page, 'calendar', '#main')
        capture(page, 'admin-calendar', 'section.panel:has(#activity-month-grid)')
        capture(page, 'admin-calendar-day', '#calendar-detail')
        visit(page, 'sessions', '#main')
        capture(page, 'admin-sessions')
        visit(page, 'tournaments', '#tournament-list')
        page.locator(f'[data-action=tournament-open][data-id="{meta["event_id"]}"]').click()
        capture(page, 'admin-event')
        page.get_by_role('tab', name='组织与协调', exact=True).click()
        page.locator('#organization-form').wait_for()
        capture(page, 'admin-event-organization', '#tournament-content')
        visit(page, 'reminders', '#broadcast-rules')
        rule = page.locator('#broadcast-rules [data-time-rule]').first
        rule.locator(':scope>summary').click()
        rule.locator('.time-conditions>summary').click()
        capture(page, 'admin-reminders', '#main')
        visit(page, 'messages', '#main')
        page.locator('#composer-form [name=channel_ids]').first.check()
        page.get_by_label('消息标题', exact=True).fill('周末活动安排')
        page.locator('#composer-text').fill('### 周末羽球活动\n\n周日下午开放报名，请在群里发送 **报名**。\n\n已报名成员请按场次到场。')
        page.get_by_role('button', name='获取发送预览', exact=True).click()
        page.locator('#message-preview strong').first.wait_for()
        capture(page, 'admin-messages', '#main')
        context.close()

        mobile = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1,
                                     is_mobile=True, has_touch=True)
        mobile.route('**/*', intercept)
        page = mobile.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.clock.install(time=datetime.fromisoformat(meta['clock']))
        page.goto(meta['forms']['390'])
        page.locator('#registration:not([hidden])').wait_for()
        with page.expect_response(lambda response: response.url.endswith('/lookup')):
            page.locator('[name=name]').fill('方宁')
            page.locator('[name=name]').press('Tab')
        page.wait_for_function("document.querySelector('[name=gender]').value==='male'")
        capture(page, 'member-confirm')
        page.locator('#registration [type=submit]').click()
        page.get_by_role('heading', name='资料已关联，注册成功', exact=True).wait_for()
        capture(page, 'member-confirmed')
        page.goto(meta['forms']['320'])
        page.reload()
        page.locator('#registration:not([hidden])').wait_for()
        page.locator('[name=name]').fill('新会员唐宁')
        page.locator('[name=gender]').select_option('female')
        page.locator('#registration [type=submit]').click()
        page.get_by_role('heading', name='申请已提交', exact=True).wait_for()
        capture(page, 'member-pending')
        page.goto(meta['profile_forms']['390'])
        page.reload()
        page.locator('#registration:not([hidden])').wait_for()
        capture(page, 'member-profile')
        mobile.close()
        browser.close()
except Exception as error:
    report['errors'].append(str(error))
finally:
    (base / 'guide-capture-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(base), **report}, ensure_ascii=False))
raise SystemExit(bool(report['errors'] or report['external_requests']))
