"""Read-only browser acceptance for both editions on a disposable static server."""
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from datetime import datetime
import threading,json
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[2]
output=root/'test-results'/('guide-editions-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
output.mkdir(parents=True)
report={'checks':[],'errors':[],'external_requests':[],'failures':[]}
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(root/'app'),**kwargs)
    def log_message(self,*args):pass
    def do_GET(self):
        path=self.path.split('?')[0]
        if path in ('/guide','/guide/member'):self.path='/static/guide.html'
        elif path=='/guide/admin':self.path='/static/guide-admin.html'
        return super().do_GET()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
origin=f'http://127.0.0.1:{server.server_port}'
def check(name,ok):
    report['checks'].append({'name':name,'passed':bool(ok)})
    if not ok:report['failures'].append(name)
def width(page,label):check(label+' no overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
def intercept(route):
    if route.request.url.startswith(origin+'/'):route.continue_()
    else:report['external_requests'].append(route.request.url);route.abort()
editions=[('/guide','普通用户版',6,['取消报名','混双','姓名匹配'],'competitions'),('/guide/admin','管理员版',7,['群关联','审核资料','结果待确认'],'large-events')]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
        try:
            for w,h in ((320,740),(390,844),(768,1024),(844,390),(1280,900)):
                context=browser.new_context(viewport={'width':w,'height':h},is_mobile=w<1000,has_touch=w<1000)
                context.route('**/*',intercept)
                page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
                for path,title,count,queries,anchor in editions:
                    label=f'{w}x{h}-{title}'
                    page.goto(origin+path);page.wait_for_load_state('networkidle')
                    check(label+' edition heading',page.locator('h1').inner_text()==title)
                    check(label+' active edition',page.locator('.edition-switch [aria-current=page]').inner_text()==title)
                    check(label+' separate chapters',page.locator('.chapter:visible').count()==count)
                    check(label+' role separation',page.locator('#large-events').count()==(1 if path.endswith('/admin') else 0))
                    width(page,label)
                    if w in (390,1280):page.screenshot(path=str(output/f'{"admin" if path.endswith("admin") else "member"}-{w}.png'))
                    page.locator('.task-guide').evaluate_all('(nodes)=>nodes.forEach(node=>node.open=true)')
                    for detail in page.locator('.response-example').all():
                        detail.locator('summary').click();check(label+' example open',detail.get_attribute('open') is not None);width(page,label+' example')
                    for detail in page.locator('.screen-guide').all():
                        detail.locator('summary').click()
                        for shot in detail.locator('img').all():
                            shot.scroll_into_view_if_needed()
                            shot.evaluate('(img)=>img.decode()')
                            check(label+' screenshot '+shot.get_attribute('alt'),shot.evaluate('img=>img.naturalWidth>0 && img.naturalHeight>0'))
                        width(page,label+' screenshots')
                    for query in queries:
                        page.locator('#search-input').fill(query)
                        check(label+' search '+query,page.locator('[data-guide-item]:visible').count()>0);width(page,label+' search')
                    page.locator('#search-input').fill('不存在abcdef');check(label+' empty',page.locator('#search-empty').is_visible())
                    page.locator(f'nav[aria-label="说明目录"] a[href="#{anchor}"]').click()
                    check(label+' nav clears search',page.locator('#search-input').input_value()=='')
                    page.wait_for_function("key=>{const y=document.getElementById(key).getBoundingClientRect().top;const h=document.querySelector('.site-header').getBoundingClientRect().bottom;return y>=h&&y<h+80}",arg=anchor)
                    check(label+' anchor visible',page.locator('#'+anchor+' h2').is_visible())
                    if w in (390,1280):page.screenshot(path=str(output/f'{anchor}-{w}.png'))
                    page.locator('.task-guide').first.evaluate('(node)=>node.open=true');page.locator('.copy-command:visible').first.click();page.locator('#copy-status').filter(has_text='复制').wait_for();check(label+' copy',bool(page.locator('#copy-status').inner_text()))
                    other='管理员版' if title=='普通用户版' else '普通用户版'
                    page.locator('.edition-switch').get_by_role('link',name=other).click();page.wait_for_load_state('networkidle')
                    check(label+' edition switch',page.locator('h1').inner_text()==other)
                context.close()
            for path,title,count,_,_ in editions:
                context=browser.new_context(viewport={'width':320,'height':740},java_script_enabled=False);context.route('**/*',intercept)
                page=context.new_page();page.goto(origin+path)
                check(title+' noJS',page.locator('.chapter:visible').count()==count)
                page.locator('.response-example').first.locator('xpath=ancestor::details[contains(@class,"task-guide")]').locator(':scope > summary').click();page.locator('.response-example').first.locator(':scope > summary').click();check(title+' noJS examples',page.locator('.response-example').first.locator('.reply-flow').is_visible());width(page,title+' noJS');context.close()
        finally:browser.close()
finally:
    server.shutdown();server.server_close();thread.join(timeout=5)
    check('no script errors',not report['errors']);check('no external requests',not report['external_requests'])
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'report':str(output/'report.json'),'checks':len(report['checks']),'failures':report['failures']},ensure_ascii=False))
raise SystemExit(bool(report['failures']))
