"""Real-browser mobile acceptance against disposable local fixtures only."""
import json,os,sys,time
from pathlib import Path
base=Path(os.environ['ROBORT_MOBILE_RUN_DIR']).resolve()
from playwright.sync_api import sync_playwright
meta=json.loads((base/'fixture.json').read_text(encoding='utf-8'))
assert meta['run_id']==os.environ['ROBORT_MOBILE_RUN_ID'], 'Stale fixture metadata'
out=base
origin=meta['origin']
report={'viewports':[],'checks':[],'console_errors':[],'external_requests':[],'failures':[]}
routes='dashboard members applications member-imports guests roles calendar venues sessions occupancies holidays daily casual stats reminders tournaments messages bots settings ai audit guide simulator'.split()
def check(label,passed,detail=None):
    report['checks'].append({'check':label,'passed':bool(passed),'detail':detail})
    if not passed:report['failures'].append({'check':label,'detail':detail})
def no_overflow(page,label):
    data=page.evaluate('''()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,body:document.body.scrollWidth,offenders:[...document.querySelectorAll('main *')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.right>innerWidth+2&&!e.closest('.table-wrap,.calendar-scroll,.tabs,.section-tabs')}).slice(0,8).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right}))})''')
    check(label+' width',data['scroll']<=data['width']+1,data)
def load(page,path):
    page.goto(origin+path);page.wait_for_load_state('networkidle')
def screenshot(page,name):page.screenshot(path=str(out/name),full_page=not name.startswith('guide-'))
with sync_playwright() as p:
    executable=os.environ.get('ROBORT_BROWSER_EXECUTABLE','').strip()
    browser=p.chromium.launch(**({'executable_path':executable} if executable else {}),headless=True,timeout=45000)
    try:
        for width,height in [(320,740),(360,800),(390,844),(768,1024),(844,390),(1280,900)]:
            label=f'{width}x{height}';report['viewports'].append(label)
            context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<1000,has_touch=width<1000)
            def intercept(route):
                if route.request.url.startswith(origin+'/'):route.continue_()
                else:report['external_requests'].append(route.request.url);route.abort()
            context.route('**/*',intercept)
            page=context.new_page();page.on('pageerror',lambda error:report['console_errors'].append(str(error)))
            load(page,'/admin');no_overflow(page,label+' login')
            page.get_by_label('管理员密码',exact=True).fill(meta['password']);page.get_by_role('button',name='登录工作台').click()
            page.wait_for_load_state('networkidle');page.locator('#main h1').wait_for()
            for route in routes:
                load(page,'/admin#'+route);page.locator('#main h1').wait_for()
                check(label+' '+route+' loaded',page.locator('#main .error-block').count()==0)
                no_overflow(page,label+' '+route)
                if route in ('members','messages','calendar','tournaments') and width in (320,390,1280):screenshot(page,f'{route}-{label}.png')
                if route=='messages':
                    page.locator('#composer-text').fill('**周末活动**\n\n| 场次 | 时间 |\n| --- | --- |\n| 第一场 | 17:30–20:30 |\n\n'+('长消息预览' * 30))
                    no_overflow(page,label+' message preview')
                if route=='guide':
                    page.locator('#ai-navigation-content').fill('批量导入会员')
                    page.locator('#ai-navigate-form button[type="submit"]').click()
                    result=page.locator('#ai-navigation-result [data-route="member-imports"]')
                    result.wait_for()
                    check(label+' backend function finder',result.is_visible())
                    no_overflow(page,label+' function finder result')
            # Navigation and focus at phone widths.
            menu=page.locator('[data-action="menu"]')
            if menu.is_visible():
                menu.click();check(label+' drawer open',menu.get_attribute('aria-expanded')=='true')
                for _ in range(18):page.keyboard.press('Tab')
                check(label+' focus trapped',page.evaluate("document.activeElement.closest('#primary-navigation')!==null"))
                page.keyboard.press('Escape');check(label+' escape closes',menu.get_attribute('aria-expanded')=='false')
                check(label+' focus returned',menu.evaluate('(el)=>el===document.activeElement'))
                menu.click();page.locator('#primary-navigation a[data-route="members"]').click();page.wait_for_load_state('networkidle')
                check(label+' route closes drawer',menu.get_attribute('aria-expanded')=='false')
                menu.click();page.mouse.click(width-8,height//2)
                check(label+' backdrop closes',menu.get_attribute('aria-expanded')=='false')
            # Use actual editable dialogs without saving mutations.
            for route,action in [('members','member-edit'),('roles','role-new'),('tournaments','tournament-new')]:
                load(page,'/admin#'+route);page.locator('#main h1').wait_for()
                button=page.locator(f'#main [data-action="{action}"]').first
                check(label+' dialog action '+action,button.count()>0)
                if not button.count():continue
                button.click();page.locator('#modal[open]').wait_for();page.wait_for_load_state('networkidle')
                size=page.locator('#modal').evaluate('(el)=>{let r=el.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:innerWidth,height:innerHeight}}')
                check(label+' dialog fits '+action,size['x']>=-1 and size['y']>=-1 and size['right']<=width+1 and size['bottom']<=height+1,size)
                if width==390:screenshot(page,f'{action}-{label}.png')
                page.locator('#modal [aria-label="关闭对话框"]').click();check(label+' dialog closes '+action,page.locator('#modal[open]').count()==0)
            # Registration: failed network read, retry, failed submit retains data, successful submission.
            regurl=meta['forms'][str(width)]
            failed={'get':False,'post':False}
            def reg_intercept(route):
                method=route.request.method
                if method=='GET' and not failed['get']:failed['get']=True;route.abort()
                elif method=='POST' and not failed['post']:failed['post']=True;route.fulfill(status=503,content_type='application/json',body='{"detail":"测试网络暂不可用，请重试"}')
                else:route.continue_()
            page.route('**/public/registration/**',reg_intercept)
            page.goto(regurl);page.wait_for_load_state('networkidle');page.get_by_role('button',name='重新读取注册状态').click();page.locator('#registration').wait_for(state='visible')
            no_overflow(page,label+' registration')
            sizes=page.locator('#registration input,#registration select,#registration button').evaluate_all('(els)=>els.map(e=>({name:e.name,font:parseFloat(getComputedStyle(e).fontSize),height:e.getBoundingClientRect().height}))')
            check(label+' registration touch controls',all(i['font']>=16 and i['height']>=44 for i in sizes),sizes)
            check(label+' guide link no token',page.locator('a[href="/guide#members"]').count()==1)
            page.locator('[name="name"]').fill('手机测试会员'+str(width));page.locator('[name="gender"]').select_option('female');page.locator('[name="level"]').select_option('6')
            page.get_by_role('button',name='提交注册申请').click();page.locator('#status').filter(has_text='测试网络').wait_for()
            check(label+' submit failure preserves name',page.locator('[name="name"]').input_value()=='手机测试会员'+str(width))
            check(label+' submit error focus',page.locator('#status').evaluate('(el)=>el===document.activeElement'))
            if width in (320,390):screenshot(page,f'registration-error-{label}.png')
            page.get_by_role('button',name='提交注册申请').click();page.locator('#result').wait_for(state='visible')
            check(label+' registration result','申请已提交' in page.locator('#result-title').inner_text())
            no_overflow(page,label+' registration result')
            if width in (320,390):screenshot(page,f'registration-success-{label}.png')
            # Public instruction page and its useful interactions.
            load(page,'/guide');no_overflow(page,label+' guide')
            check(label+' guide chapters',all(page.locator('#'+chapter).is_visible() for chapter in ('start','members','competitions','events','records','feedback')))
            if width in (320,390,768,1280):screenshot(page,f'guide-{label}.png')
            page.locator('#search-input').fill('混双');check(label+' guide search',page.locator('[data-guide-item]:visible').count()>0)
            page.locator('#search-input').fill('不存在的功能abcdef');check(label+' guide empty search',page.locator('#search-empty').is_visible())
            page.locator('nav[aria-label="说明目录"] a[href="#competitions"]').click()
            check(label+' guide anchor resets search',page.locator('#search-input').input_value()=='' and page.locator('#competitions').is_visible())
            page.wait_for_function("document.querySelector('#competitions').getBoundingClientRect().top < 190")
            check(label+' guide navigates to competition',page.locator('#competitions h2').evaluate('(el)=>el.getBoundingClientRect().top < innerHeight'))
            if width in (320,390,768,1280):screenshot(page,f'guide-competitions-{label}.png')
            page.locator('.copy-command').first.click();page.locator('#copy-status').filter(has_text='复制').wait_for()
            check(label+' guide copy feedback',bool(page.locator('#copy-status').inner_text()))
            context.close();print('PASS viewport',label,flush=True)
        context=browser.new_context(viewport={'width':320,'height':740},java_script_enabled=False)
        context.route('**/*',intercept)
        page=context.new_page();page.on('pageerror',lambda error:report['console_errors'].append(str(error)));load(page,'/guide')
        check('no JavaScript guide readable',all(page.locator('#'+chapter).is_visible() for chapter in ('start','members','competitions','events','records','feedback')))
        no_overflow(page,'no JavaScript guide');context.close()
    except Exception as error:
        report['failures'].append({'check':'browser execution','detail':str(error)})
        try:screenshot(page,'failure.png')
        except Exception:pass
        raise
    finally:
        check('no browser errors',not report['console_errors'],report['console_errors'])
        check('no external network requests',not report['external_requests'],report['external_requests'])
        (out/'mobile-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
print(json.dumps({'checks':len(report['checks']),'failures':report['failures']},ensure_ascii=False),flush=True)
raise SystemExit(bool(report['failures']))
