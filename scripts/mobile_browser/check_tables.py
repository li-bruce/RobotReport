"""Verify shared admin numbering against actual pages and dynamic table updates."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base = Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta = json.loads((base / 'fixture.json').read_text(encoding='utf-8'))
origin = meta['origin']
report = {'checks': [], 'errors': [], 'external_requests': []}


def check(label, passed):
    report['checks'].append({'name': label, 'passed': bool(passed)})
    assert passed, label


def intercept(route):
    if route.request.url.startswith(origin + '/'):
        route.continue_()
    else:
        report['external_requests'].append(route.request.url)
        route.abort()


def tables(page, label, root='#main'):
    page.locator('#main .loading').wait_for(state='hidden')
    page.wait_for_load_state('networkidle')
    found = page.locator(root + ' .table-wrap > table')
    check(label + ' has data table', found.count() > 0)
    for i in range(found.count()):
        table = found.nth(i)
        check(label + ' leading header', table.locator('thead th').first.inner_text() == '序号')
        rows = table.locator('tbody > tr').all()
        check(label + ' sequence', [row.locator('td').first.inner_text() for row in rows] == [str(n + 1) for n in range(len(rows))])
        check(label + ' single number column', table.locator('thead .admin-row-number').count() == 1)
        check(label + ' columns align', all(row.locator('td').count() == table.locator('thead th').count() for row in rows))
    check(label + ' page fits', page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))


def visit(page, route):
    if page.locator('#modal[open]').count():
        page.keyboard.press('Escape')
    page.goto(origin + '/admin#' + route)
    page.locator('#main .loading').wait_for(state='hidden')
    page.wait_for_load_state('networkidle')


try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'), headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900})
        context.route('**/*', intercept)
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        page.clock.install(time=datetime.fromisoformat(meta['clock']))
        page.goto(origin + '/admin#members')
        page.get_by_label('管理员密码', exact=True).fill(meta['password'])
        page.get_by_role('button', name='登录工作台', exact=True).click()
        page.locator('#member-list table').wait_for()
        for width in (1280, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 900})
            visit(page, 'members')
            tables(page, f'{width} members')
            check('compact number column', page.locator('th.admin-row-number').first.bounding_box()['width'] < 70)
            page.locator('#member-search').fill('张三')
            tables(page, f'{width} filtered members')
            check('filter keeps identity', page.locator('#member-list td.name').all_inner_texts() == ['张三'])
            page.locator('#member-search').fill('不存在的会员')
            check('empty state is not a record', page.locator('#member-list table').count() == 0)
            page.locator('#member-search').fill('')
            page.locator('#member-status-filter').select_option('pending')
            tables(page, f'{width} pending')
            page.locator('#member-list').screenshot(path=str(base / f'rows-{width}.png'))
            for route in ('member-imports', 'guests', 'roles', 'stats', 'audit'):
                visit(page, route)
                tables(page, f'{width} {route}')
            visit(page, 'member-imports')
            page.locator('#import-select-all').check()
            selected = page.locator('[data-import-check]:checked').count()
            check('select-all identity unchanged', selected == page.locator('[data-import-check]').count() and selected > 0)
            page.locator('#import-select-all').uncheck()
            check('clear selection', page.locator('[data-import-check]:checked').count() == 0)
            page.get_by_role('button', name='匹配已有会员', exact=True).click()
            page.locator('#modal table').wait_for()
            tables(page, f'{width} matching modal', '#modal')
            visit(page, 'tournaments')
            page.locator(f'[data-action=tournament-open][data-id="{meta["proxy_event_id"]}"]').click()
            page.get_by_role('tab', name='参赛名单', exact=True).click()
            tables(page, f'{width} participants')

        # Async modal content, reordering, append/remove, hidden rows and spans.
        page.evaluate('''() => {
          openModal('序号验证', '<div class="table-wrap"><table><thead><tr><th>姓名</th><th>动作</th></tr></thead><tbody id="rows-test"><tr data-key="a"><td>甲</td><td><button data-key="a">编辑</button></td></tr><tr data-key="b"><td>乙</td><td><button data-key="b">编辑</button></td></tr></tbody></table></div>');
        }''')
        tables(page, 'dynamic modal', '#modal')
        page.evaluate("document.querySelector('#rows-test').prepend(document.querySelector('#rows-test tr:last-child'))")
        tables(page, 'reordered modal', '#modal')
        check('reorder preserves key', page.locator('#rows-test tr').first.get_attribute('data-key') == 'b')
        page.evaluate("document.querySelector('#rows-test').insertAdjacentHTML('beforeend','<tr data-key=\"c\"><td>丙</td><td>查看</td></tr>')")
        tables(page, 'appended row', '#modal')
        page.locator('#rows-test tr').first.evaluate('(row) => row.hidden = true')
        check('hidden row does not consume number', page.locator('#rows-test tr:visible .admin-row-number').all_inner_texts() == ['1', '2'])
        page.locator('#rows-test tr').first.evaluate('(row) => row.hidden = false')
        tables(page, 'unhidden row', '#modal')
        page.locator('#rows-test tr').nth(1).evaluate('(row) => row.remove()')
        tables(page, 'removed row', '#modal')
        page.evaluate("document.querySelector('#rows-test').innerHTML='<tr><td colspan=\"2\">暂无记录</td></tr>'")
        check('empty row spans number column', page.locator('#rows-test td').get_attribute('colspan') == '3')
        check('empty row not numbered', page.locator('#rows-test .admin-row-number').count() == 0)
        # Large lists remain stable after the observer settles; no repeated columns.
        page.evaluate("document.querySelector('#rows-test').innerHTML=Array.from({length:1000},(_,i)=>'<tr><td>会员'+i+'</td><td>查看</td></tr>').join('')")
        page.wait_for_function("document.querySelector('#rows-test tr:last-child td').textContent === '1000'")
        check('large list one cell each', page.locator('#rows-test .admin-row-number').count() == 1000)
        page.evaluate("openModal('消息预览',markdown('| 姓名 | 性别 |\\n|---|---|\\n| 张三 | 男 |'))")
        check('message preview retains columns', page.locator('#modal th').all_inner_texts() == ['姓名', '性别'])
        page.keyboard.press('Escape')
        page.goto(origin + '/guide/admin')
        check('manual examples retain format', page.locator('.admin-row-number').count() == 0)
        browser.close()
except Exception as error:
    report['errors'].append(str(error))
finally:
    (base / 'tables-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(base), 'checks': len(report['checks']), 'errors': report['errors'], 'external_requests': report['external_requests']}, ensure_ascii=False))
raise SystemExit(bool(report['errors'] or report['external_requests']))
