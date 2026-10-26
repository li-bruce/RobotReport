"""Read-only presentation checks on the disposable local fixture."""
import json,os,sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from playwright.sync_api import sync_playwright

base=Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta=json.loads((base/'fixture.json').read_text(encoding='utf8'))
origin=meta['origin']
report={'checks':[],'errors':[],'external_requests':[]}

def check(label,ok):
    report['checks'].append({'name':label,'passed':bool(ok)})
    assert ok,label

def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()

def fit(page,label):check(label,page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))

try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'),headless=True)
        for w,h in ((390,844),(844,390),(1280,900)):
            ctx=browser.new_context(viewport={'width':w,'height':h})
            ctx.route('**/*',intercept)
            page=ctx.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
            page.clock.install(time=datetime.fromisoformat(meta['clock']))
            page.goto(origin+'/admin#dashboard')
            page.get_by_label('管理员密码',exact=True).fill(meta['password'])
            page.get_by_role('button',name='登录工作台',exact=True).click()
            page.locator('.dashboard-layout').wait_for()
            check('three navigation groups',page.locator('.nav-section').count()==3)
            check('no decorative heading',not page.locator('.page-heading .eyebrow').count())
            check('concise activity history',page.locator('.timeline li').count()<=5)
            fit(page,'dashboard fits '+str(w))
            page.screenshot(path=str(base/f'dashboard-{w}.png'),full_page=True)
            if w<950:
                page.get_by_role('button',name='展开导航',exact=True).click()
                page.locator('.nav a[data-route=members]').click()
                check('drawer closes after navigation',page.get_by_role('button',name='展开导航',exact=True).get_attribute('aria-expanded')=='false')
            else:page.locator('.nav a[data-route=members]').click()
            page.locator('#member-list').wait_for()
            check('context follows route',page.locator('.route-context').inner_text()=='人员中心')
            fit(page,'members fits '+str(w))
            page.screenshot(path=str(base/f'members-{w}.png'),full_page=True)
            for path in ('/guide/member','/guide/admin'):
                page.goto(origin+path);page.locator('.copy-command').first.wait_for(state='attached')
                edition='admin' if path.endswith('admin') else 'member'
                check(edition+' tasks collapsed',page.locator('.task-guide[open]').count()==0)
                check(edition+' chapter selection matches start',page.locator('.contents [aria-current=location]').get_attribute('href')=='#start')
                check(edition+' color key',page.locator('.command-key .command-parameter').count()==1)
                fit(page,edition+' overview fits '+str(w))
                page.screenshot(path=str(base/f'{edition}-guide-{w}.png'))
                page.locator('.task-guide').first.locator(':scope>summary').click()
                check(edition+' task opens',page.locator('.task-guide').first.get_attribute('open') is not None)
                page.keyboard.press('Tab')
                page.locator('#search-input').fill('张三')
                check(edition+' search opens matching tasks',page.locator('[data-guide-item]:visible .task-guide:not([open])').count()==0)
                fit(page,edition+' results fit '+str(w))
                page.locator('#clear-search').click()
                check(edition+' clears restores original disclosure',page.locator('.task-guide[open]').count()==1)
                anchor='registration-admin' if edition=='admin' else 'registration-flow'
                page.goto(origin+path+'#'+anchor)
                page.locator('#'+anchor+' > .task-guide[open]').wait_for()
                check(edition+' deep link visible',page.locator('#'+anchor).evaluate('e=>e.getBoundingClientRect().top>=60'))
                page.screenshot(path=str(base/f'{edition}-task-{w}.png'))
                # Copy a command containing a parameter, preserving exact plain text.
                page.locator('#search-input').fill('张三')
                code=page.locator('code[data-command]:visible').filter(has_text='张三').first
                check(edition+' parameter colored',code.locator('.command-parameter').count()>0)
                raw=code.inner_text()
                page.evaluate('''() => { window.copiedText=''; Object.defineProperty(navigator,'clipboard',{value:{writeText:async(text)=>{window.copiedText=text;}}}); document.execCommand=(name)=>{window.copiedText=document.querySelector('textarea.clipboard-fallback').value;return true;}; }''')
                code.locator('xpath=..').get_by_role('button',name='复制指令：'+raw,exact=True).click()
                check(edition+' parameters stay inline',raw==code.text_content())
                check(edition+' copied text intact',page.evaluate('window.copiedText')==raw)
                check(edition+' copy has no tags','<span' not in raw and '<font' not in raw)
                # Clearing search and back navigation keep the target reachable.
                page.locator('#clear-search').click()
                page.goto(origin+path+'#feedback');page.locator('#feedback h2').wait_for()
                fit(page,edition+' feedback fits '+str(w))
            ctx.close()
        # The help renderer is used in previews as well as bot responses.
        ctx=browser.new_context(viewport={'width':390,'height':844});ctx.route('**/*',intercept)
        page=ctx.new_page();page.goto(origin+'/admin')
        page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台',exact=True).click();page.locator('#main h1').wait_for()
        from app.help import command_help
        text=command_help({'status':'approved'},category='报名',capabilities=['enrollment'])
        page.evaluate('value=>{document.querySelector("#main").innerHTML="<section class=panel><div class=panel-body id=help-preview>"+markdown(value)+"</div></section>";}',text)
        check('help preview colors',page.locator('#help-preview span[style*=color]').count()>4)
        check('help preview has no literal font tags','<font' not in page.locator('#help-preview').inner_text())
        fit(page,'help preview fits');page.screenshot(path=str(base/'bot-help-390.png'),full_page=True)
        ctx.close();browser.close()
    check('no errors',not report['errors']);check('no external requests',not report['external_requests'])
finally:
    (base/'interface-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'output':str(base),'checks':len(report['checks'])},ensure_ascii=False))
