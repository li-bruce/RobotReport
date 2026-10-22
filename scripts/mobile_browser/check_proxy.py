"""Real-browser roster and precise guest cancellation checks on local fixtures."""
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

output=Path(os.environ['ROBORT_MOBILE_RUN_DIR']).resolve()
meta=json.loads((output/'fixture.json').read_text(encoding='utf-8'))
assert meta['run_id']==os.environ['ROBORT_MOBILE_RUN_ID'], 'Stale fixture metadata'
origin=meta['origin']
report={'viewports':[],'checks':[],'failures':[],'page_errors':[],'external_requests':[],'screenshots':[],'deleted_entries':[]}

def check(label, passed, detail=None):
    report['checks'].append({'check':label,'passed':bool(passed),'detail':detail})
    if not passed:report['failures'].append({'check':label,'detail':detail})

def shot(page,name):
    path=output/name
    page.screenshot(path=str(path),full_page=True)
    report['screenshots'].append(str(path))

def width_check(page,label):
    sizes=page.evaluate('()=>({viewport:innerWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth})')
    check(label+' no page overflow',sizes['document']<=sizes['viewport']+1 and sizes['body']<=sizes['viewport']+1,sizes)

def headers_check(table,label):
    headers=table.locator('thead th').all_inner_texts()
    check(label+' number name gender quota columns',headers[:4]==['序号','姓名','性别','名额'],headers)
    check(label+' no registration status column',not any(h.strip() in ('状态','报名状态') for h in headers),headers)

def api(context,path):
    response=context.request.get(origin+path)
    if not response.ok:raise RuntimeError(f'Fixture GET failed: {path} {response.status}')
    return response.json()

with sync_playwright() as playwright:
    executable=os.environ.get('ROBORT_BROWSER_EXECUTABLE','').strip()
    browser=playwright.chromium.launch(**({'executable_path':executable} if executable else {}),headless=True,timeout=45000)
    try:
        for width,height in ((320,740),(360,800),(390,844),(768,1024),(844,390),(1280,900)):
            label=f'{width}x{height}'
            report['viewports'].append(label)
            context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<1000,has_touch=width<1000)
            def intercept(route):
                if route.request.url.startswith(origin+'/'):route.continue_()
                else:report['external_requests'].append(route.request.url);route.abort()
            context.route('**/*',intercept)
            page=context.new_page()
            page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
            page.on('requestfailed',lambda req:report['page_errors'].append('Request failed: '+req.url.split('?')[0]+' '+str(req.failure)))
            page.on('response',lambda res:report['page_errors'].append('HTTP '+str(res.status)+' '+res.url.split('?')[0]) if res.status>=400 and '/static/' in res.url else None)
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin');page.wait_for_load_state('networkidle')
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('#main h1').wait_for();page.wait_for_load_state('networkidle')
            # Open the first activity through the actual dashboard action.
            page.locator('[data-action="day-open"]').first.click()
            table=page.locator('#calendar-detail table')
            table.wait_for();page.wait_for_load_state('networkidle')
            headers_check(table,label+' daily roster')
            proxy=table.locator('tbody tr').filter(has_text='随行甲（张三代报名）')
            check(label+' daily proxy sponsor',proxy.count()==1)
            check(label+' guest gender unknown',proxy.locator('td').nth(2).inner_text()=='未填写')
            owner=table.locator('tbody tr').filter(has=page.locator('button[data-name="张三"]'))
            check(label+' member actual gender',owner.locator('td').nth(2).inner_text()=='男')
            check(label+' ranked quota visible',any(value in table.inner_text() for value in ('正式','替补')))
            width_check(page,label+' daily roster')
            if width in (320,390,844,1280):shot(page,f'proxy-daily-{label}.png')
            # Open the large event and inspect the real participant table.
            page.goto(origin+'/admin#tournaments');page.wait_for_load_state('networkidle')
            page.locator(f'[data-action="tournament-open"][data-id="{meta["proxy_event_id"]}"]').click()
            page.get_by_role('tab',name='参赛名单',exact=True).click()
            table=page.locator('#tournament-content table');table.wait_for();page.wait_for_load_state('networkidle')
            headers_check(table,label+' tournament roster')
            target=meta['proxy_targets'][str(width)]
            row=table.locator('tbody tr').filter(has_text=target['name']+'（张三代报名）')
            check(label+' event proxy sponsor',row.count()==1)
            check(label+' event guest gender unknown',row.locator('td').nth(2).inner_text()=='未填写')
            check(label+' cancel action targets participant id',row.get_by_role('button',name='取消报名',exact=True).get_attribute('data-id')==target['id'])
            width_check(page,label+' event roster')
            if width in (320,390,844,1280):shot(page,f'proxy-event-before-{label}.png')
            before=api(context,f'/api/tournaments/{meta["proxy_event_id"]}')
            summary=page.locator('.summary-line span').filter(has_text='参赛者').locator('strong')
            check(label+' top summary counts active before cancellation',int(summary.inner_text())==before['participant_count'],{'display':summary.inner_text(),'active':before['participant_count']})
            row.get_by_role('button',name='取消报名',exact=True).click()
            modal=page.locator('#modal[open]');modal.wait_for()
            check(label+' cancellation confirmation names exact guest',target['name']+'（张三代报名）' in modal.inner_text())
            modal_size=modal.bounding_box()
            check(label+' cancel dialog fits',modal_size['x']>=-1 and modal_size['y']>=-1 and modal_size['x']+modal_size['width']<=width+1 and modal_size['y']+modal_size['height']<=height+1,modal_size)
            if width==390:shot(page,'proxy-cancel-confirm-390x844.png')
            expected=f'/api/tournaments/{meta["proxy_event_id"]}/entries/'+quote(target['id'],safe='')
            with page.expect_response(lambda response:response.request.method=='DELETE' and response.url.endswith(expected)) as response:
                modal.get_by_role('button',name='确认取消',exact=True).click()
            check(label+' precise deletion endpoint success',response.value.status==200)
            page.locator('#modal[open]').wait_for(state='hidden');page.wait_for_load_state('networkidle')
            after=api(context,f'/api/tournaments/{meta["proxy_event_id"]}')
            summary=page.locator('.summary-line span').filter(has_text='参赛者').locator('strong')
            check(label+' top summary counts active after cancellation',int(summary.inner_text())==after['participant_count'],{'display':summary.inner_text(),'active':after['participant_count']})
            old={row['id']:row for row in before['participants']}
            new={row['id']:row for row in after['participants']}
            changed=[key for key in old if old[key]!=new.get(key)]
            check(label+' only selected guest changes',changed==[target['id']],changed)
            check(label+' selected guest canceled',new[target['id']]['status']=='cancelled')
            check(label+' owner and retained guest active',new[meta['proxy_owner_id']]['status']=='active' and new[meta['proxy_keep_id']]['status']=='active')
            check(label+' all historical rows retained',set(old)==set(new))
            check(label+' active count drops by one',after['participant_count']==before['participant_count']-1)
            report['deleted_entries'].append({'viewport':label,'participant_id':target['id'],'name':target['name']})
            width_check(page,label+' after cancellation')
            if width in (320,390):shot(page,f'proxy-event-after-{label}.png')
            context.close()
            print('PASS proxy viewport',label,flush=True)
    except Exception as error:
        report['failures'].append({'check':'browser execution','detail':str(error)})
        try:shot(page,'proxy-failure.png')
        except Exception:pass
        raise
    finally:
        check('no page errors',not report['page_errors'],report['page_errors'])
        check('no external requests',not report['external_requests'],report['external_requests'])
        report['summary']={'viewports':len(report['viewports']),'checks':len(report['checks']),
                           'passed':sum(item['passed'] for item in report['checks']),
                           'failures':len(report['failures']),'canceled_entries':len(report['deleted_entries'])}
        (output/'proxy-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'checks':len(report['checks']),'failures':report['failures'],'report':str(output/'proxy-report.json')},ensure_ascii=False),flush=True)
raise SystemExit(bool(report['failures']))
