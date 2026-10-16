"""Real-browser robot capabilities and same-group rebind on isolated fixtures."""
import json
import os
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

output = Path(os.environ['ROBORT_MOBILE_RUN_DIR']).resolve()
meta = json.loads((output / 'fixture.json').read_text(encoding='utf-8'))
assert meta['run_id'] == os.environ['ROBORT_MOBILE_RUN_ID'], 'Stale fixture metadata'
origin = meta['origin']
report = {'viewports': [], 'checks': [], 'failures': [], 'page_errors': [], 'external_requests': [], 'screenshots': []}


def check(label, passed, detail=None):
    row = {'check': label, 'passed': bool(passed), 'detail': detail}
    report['checks'].append(row)
    if not passed:
        report['failures'].append(row)


def get(context, path):
    response = context.request.get(origin + path)
    if not response.ok:
        raise RuntimeError(f'Fixture GET failed: {path} {response.status}')
    value = response.json()
    if isinstance(value, list):
        return {'channels' if 'channels' in path else 'bots': value}
    return value


def fits(page, label):
    sizes = page.evaluate('()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth})')
    check(label + ' no page overflow', sizes['document'] <= sizes['viewport'] + 1 and sizes['body'] <= sizes['viewport'] + 1, sizes)
    modal = page.locator('#modal[open]')
    if modal.count() and modal.is_visible():
        bounds = modal.bounding_box()
        viewport = page.viewport_size
        check(label + ' dialog fits screen', bounds['x'] >= 0 and bounds['y'] >= 0 and bounds['x'] + bounds['width'] <= viewport['width'] + 1 and bounds['y'] + bounds['height'] <= viewport['height'] + 1, bounds)


def close(page):
    page.locator('#modal .modal-close').click()


def save(page, label):
    button = page.get_by_role('button', name=label, exact=True)
    form_id = button.evaluate("el=>el.closest('form').id")
    with page.expect_response(lambda response: response.request.method in ('POST','PATCH','DELETE') and '/api/' in response.url) as sent:
        button.click()
    response = sent.value
    if not response.ok:
        raise RuntimeError(response.text())
    page.locator('#' + form_id).wait_for(state='detached')
    page.locator('.robot-card').first.wait_for()



with sync_playwright() as playwright:
    executable = os.environ.get('ROBORT_BROWSER_EXECUTABLE', '').strip()
    browser = playwright.chromium.launch(**({'executable_path': executable} if executable else {}), headless=True, timeout=45000)
    try:
        for width, height in ((320,740), (390,844), (844,390), (1280,900)):
            label = f'{width}x{height}'
            report['viewports'].append(label)
            context = browser.new_context(viewport={'width': width, 'height': height}, is_mobile=width < 1000, has_touch=width < 1000)
            def intercept(route):
                if route.request.url.startswith(origin + '/'):
                    route.continue_()
                else:
                    report['external_requests'].append(route.request.url)
                    route.abort()
            context.route('**/*', intercept)
            page = context.new_page()
            page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin + '/admin#bots')
            page.wait_for_load_state('networkidle')
            page.get_by_label('管理员密码', exact=True).fill(meta['password'])
            page.get_by_role('button', name='登录工作台', exact=True).click()
            page.locator('.robot-card').first.wait_for()
            page.wait_for_load_state('networkidle')
            fits(page, label + ' robot list')
            check(label + ' bots collapsed initially', page.locator('.robot-card[open]').count() == 0)
            check(label + ' detail actions hidden initially', not page.get_by_role('button', name='编辑机器人', exact=True).count())
            shared_cluster = page.locator('.robot-cluster[data-chat-id="mobile-fixture-group"]')
            check(label + ' same group contains multiple bots', shared_cluster.locator('.robot-card').count() >= 2)
            cluster_summary = shared_cluster.locator('.robot-cluster-summary')
            cluster_summary.focus()
            page.keyboard.press('Enter')
            check(label + ' group keyboard collapse', not shared_cluster.locator('.robot-cluster-body').is_visible())
            page.keyboard.press('Enter')
            check(label + ' group keyboard expand', shared_cluster.locator('.robot-cluster-body').is_visible())
            check(label + ' no secrets on list', page.locator('input[type=password]').count() == 0)
            baseline = get(context, '/api/channels')['channels']
            baseline_ids = {c['id'] for c in baseline}
            page.get_by_role('button', name='添加机器人', exact=True).click()
            page.locator('#bot-form').wait_for()
            page.get_by_label('机器人名称', exact=True).fill('学生验收机器人 ' + label)
            daily = page.locator('#bot-form input[value=daily_events]')
            check(label + ' daily always enabled', daily.is_checked() and daily.is_disabled())
            for choice in page.locator('#bot-form input[name=capabilities]:enabled').all():
                choice.set_checked(choice.get_attribute('value') in ('registration', 'statistics'))
            fits(page, label + ' duties form')
            save(page, '创建机器人')
            page.locator('#channel-form').wait_for()
            check(label + ' new bot continues to group binding', page.get_by_label('群 ID', exact=True).input_value() == '')
            check(label + ' binding settings collapsed', page.locator('#channel-form details[open]').count() == 0)
            page.get_by_label('群名称', exact=True).fill('学生原群 ' + label)
            page.get_by_label('群 ID', exact=True).fill('student-original-' + str(width))
            page.get_by_label('发送 Webhook 地址', exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key=browser-original-' + str(width))
            check(label + ' no repeated group scopes',page.locator('#channel-form [name=slot_mode],#channel-form [name=venue_ids]').count()==0)
            fits(page, label + ' add group')
            save(page, '保存群配置')
            page.get_by_label('回调地址', exact=True).wait_for()
            callback = page.get_by_label('回调地址', exact=True).input_value()
            check(label + ' own callback provided', '/kingsoft/callback/' in callback)
            close(page)
            bot_id = next(b['id'] for b in get(context, '/api/bots')['bots'] if b['name'] == '学生验收机器人 ' + label)
            card = page.locator(f'.robot-card[data-bot-id="{bot_id}"]')
            card.locator('.robot-card-summary').focus()
            page.keyboard.press('Space')
            check(label + ' robot keyboard expand', card.get_attribute('open') is not None)
            check(label + ' connection details stay collapsed', not card.locator('.robot-connection').get_attribute('open'))
            fits(page, label + ' expanded robot')
            current = next(b for b in get(context, '/api/bots')['bots'] if b['id'] == bot_id)
            check(label + ' selected capabilities persisted', set(current['capabilities']) == {'daily_events', 'registration', 'statistics'}, current['capabilities'])
            card.get_by_role('button', name='帮助预览', exact=True).click()
            preview = page.locator('#bot-help-preview')
            preview.wait_for()
            check(label + ' contextual member help', 'AI ' not in preview.inner_text() and '组织比赛' in preview.inner_text(), preview.inner_text())
            with page.expect_response(lambda response: 'help-preview?audience=unregistered' in response.url):
                page.get_by_label('查看身份', exact=True).select_option('unregistered')
            preview.get_by_text('注册', exact=False).first.wait_for()
            check(label + ' registration help', '注册' in preview.inner_text())
            fits(page, label + ' help preview')
            close(page)
            card.get_by_role('button', name='编辑机器人', exact=True).click()
            page.locator('#bot-form input[value=enrollment]').check()
            page.locator('#bot-form input[value=ai]').check()
            save(page, '保存机器人')
            check(label + ' expanded robot persists after edit', card.get_attribute('open') is not None)
            check(label + ' bound bot has no add group action', card.get_by_role('button', name='绑定群', exact=True).count() == 0)
            old_id = get(context, f'/api/bots/{bot_id}/channels')['channels'][0]['id']
            card.get_by_role('button', name='编辑连接', exact=True).click()
            page.get_by_label('群名称', exact=True).fill('学生改名群 ' + label)
            check(label + ' secrets blank on edit', page.get_by_label('发送 Webhook 地址', exact=True).input_value() == '' and page.get_by_label('自定义 Robot Key（通常不用填写）', exact=True).input_value() == '')
            save(page, '保存群配置')
            check(label + ' rename retains configured credentials', get(context, f'/api/bots/{bot_id}/channels')['channels'][0]['configured'])
            card.get_by_role('button', name='编辑连接', exact=True).click()
            page.get_by_label('选择已接入的群', exact=True).select_option('mobile-fixture-group')
            check(label + ' same-group selector fills target', page.get_by_label('群 ID', exact=True).input_value() == 'mobile-fixture-group')
            check(label + ' rebind asks fresh credentials', page.get_by_label('发送 Webhook 地址', exact=True).get_attribute('required') is not None and page.locator('#channel-rebind-note').is_visible())
            page.get_by_label('发送 Webhook 地址', exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key=browser-rebound-' + str(width))

            fits(page, label + ' shared group edit')
            save(page, '保存群配置')
            page.get_by_label('回调地址',exact=True).wait_for()
            close(page)
            rows = get(context, f'/api/bots/{bot_id}/channels')['channels']
            check(label + ' shared group rebound', len(rows) == 1 and rows[0]['chat_id'] == 'mobile-fixture-group' and rows[0]['id'] != old_id, rows)
            history = get(context, f'/api/bots/{bot_id}/channels?include_archived=1')['channels']
            check(label + ' original group archived', any(c['id'] == old_id and c['archived'] and c['chat_id'] == 'student-original-' + str(width) for c in history))
            shared_cluster = page.locator('.robot-cluster[data-chat-id="mobile-fixture-group"]')
            check(label + ' rebound bot grouped with existing bots', shared_cluster.locator(f'.robot-card[data-bot-id="{bot_id}"]').count() == 1)
            check(label + ' new binding starts collapsed', card.get_attribute('open') is None)
            card.locator('.robot-card-summary').click()
            card.locator('.robot-connection > summary').click()
            group = card.locator('.robot-group')
            card.get_by_role('button', name='已移除的群', exact=True).click()
            check(label + ' bound bot cannot reconnect a second group', page.get_by_role('button', name='重新接入', exact=True).count() == 0)
            close(page)
            with page.expect_response(lambda response: response.request.method == 'PATCH' and '/channels/' in response.url):
                group.get_by_role('button', name='暂停', exact=True).click()
            group.get_by_role('button', name='启用', exact=True).wait_for()
            check(label + ' pause saved', get(context, f'/api/bots/{bot_id}/channels')['channels'][0]['active'] is False)
            with page.expect_response(lambda response: response.request.method == 'PATCH' and '/channels/' in response.url):
                group.get_by_role('button', name='启用', exact=True).click()
            group.get_by_role('button', name='暂停', exact=True).wait_for()
            group.get_by_role('button', name='移除', exact=True).click()
            check(label + ' removal explains history preservation', '历史记录和同群其他机器人保留' in page.locator('#modal').inner_text())
            save(page, '确认移除')
            check(label + ' removed binding absent', not get(context, f'/api/bots/{bot_id}/channels')['channels'])
            after = get(context, '/api/channels')['channels']
            check(label + ' other bots unchanged', all(c in after for c in baseline if c['id'] in baseline_ids))
            card.locator('.robot-card-summary').click()
            card.locator('.robot-connection > summary').click()
            card.get_by_role('button', name='已移除的群', exact=True).click()
            page.get_by_role('button', name='重新接入', exact=True).first.wait_for()
            check(label + ' both historical bindings visible', page.locator('.robot-archived-list .robot-group').count() == 2)
            page.get_by_role('button', name='重新接入', exact=True).first.click()
            check(label + ' reconnect prefilled group', bool(page.get_by_label('群 ID', exact=True).input_value()))
            page.get_by_label('发送 Webhook 地址', exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key=browser-reconnect-' + str(width))

            save(page, '保存群配置')
            page.get_by_label('回调地址',exact=True).wait_for()
            close(page)
            check(label + ' reconnect creates current binding', len(get(context, f'/api/bots/{bot_id}/channels')['channels']) == 1)
            shared_cluster.get_by_role('button', name='添加机器人到此群', exact=True).click()
            page.get_by_label('机器人名称', exact=True).fill('同群新增助手 ' + label)
            check(label + ' scope settings collapsed', page.locator('#bot-form details[open]').count() == 0)
            save(page, '创建机器人')
            check(label + ' group shortcut preselects group', page.get_by_label('群 ID', exact=True).input_value() == 'mobile-fixture-group')
            check(label + ' group shortcut never copies credentials', page.get_by_label('发送 Webhook 地址', exact=True).input_value() == '')
            page.get_by_label('发送 Webhook 地址', exact=True).fill('https://xz.wps.cn/api/v1/webhook/send?key=browser-shortcut-' + str(width))
            save(page, '保存群配置')
            page.get_by_label('回调地址', exact=True).wait_for()
            close(page)
            check(label + ' targeted creation joins shared group', shared_cluster.get_by_text('同群新增助手 ' + label, exact=True).count() == 1)
            fits(page, label + ' complete robot card')
            while page.get_by_role('button', name='关闭通知', exact=True).count():
                page.get_by_role('button', name='关闭通知', exact=True).first.click()
            shot = output / ('bots-' + label + '.png')
            page.screenshot(path=str(shot), full_page=True)
            report['screenshots'].append(str(shot))
            if card.get_attribute('open') is None:
                card.locator('.robot-card-summary').click()
            fits(page, label + ' final expanded card')
            expanded = output / ('bots-expanded-' + label + '.png')
            card.screenshot(path=str(expanded))
            report['screenshots'].append(str(expanded))
            context.close()
    except Exception as error:
        report['failures'].append({'check': 'browser run', 'detail': str(error)})
        raise
    finally:
        browser.close()
        check('no JavaScript errors', not report['page_errors'], report['page_errors'])
        check('no external requests', not report['external_requests'], report['external_requests'])
        (output / 'bots-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'checks': len(report['checks']), 'failures': len(report['failures']), 'report': str(output / 'bots-report.json')}, ensure_ascii=False))

raise SystemExit(1 if report['failures'] else 0)
