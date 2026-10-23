"""Exercise real image loading, failure recovery and responsive races locally."""
import json
import os
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

base = Path(os.environ['ROBORT_MOBILE_RUN_DIR'])
meta = json.loads((base / 'fixture.json').read_text(encoding='utf8'))
origin = meta['origin']
root = Path(__file__).resolve().parents[2]
report = {'checks': [], 'errors': [], 'external_requests': []}
# The system-font screen is captured while its background request deliberately
# keeps the document loading; do not wait for document.fonts.ready here.
os.environ['PW_TEST_SCREENSHOT_NO_FONTS_READY'] = '1'


def check(name, value):
    report['checks'].append({'name': name, 'passed': bool(value)})
    assert value, name


class Scene:
    def __init__(self, browser, width=1280, height=900, reduced_motion='no-preference'):
        self.context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion=reduced_motion)
        self.pending = []
        self.requests = []
        self.serve = set()
        self.fail = False
        self.context.route('**/*', self.intercept)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: report['errors'].append(str(error)))
        self.page.clock.install(time=datetime.fromisoformat(meta['clock']))

    def intercept(self, route):
        url = route.request.url
        if not url.startswith(origin + '/'):
            report['external_requests'].append(url)
            route.abort()
        elif '/brand/gatherspace-login-' in url:
            kind = 'mobile' if '-mobile.png' in url else 'desktop'
            self.requests.append(kind)
            if self.fail:
                route.abort()
            elif kind in self.serve:
                self.fulfill(route, kind)
            else:
                self.pending.append((route, kind))
        else:
            route.continue_()

    def fulfill(self, route, kind):
        route.fulfill(status=200, content_type='image/png', body=(root / f'app/static/brand/gatherspace-login-{kind}.png').read_bytes())

    def release(self, kind):
        self.serve.add(kind)
        waiting, self.pending = self.pending, []
        for route, asset in waiting:
            if asset == kind:
                self.fulfill(route, asset)
            else:
                self.pending.append((route, asset))

    def open(self):
        self.page.goto(origin + '/admin#dashboard', wait_until='domcontentloaded')
        self.page.locator('.gather-login').wait_for()
        # Session restoration may replace the initial sign-in root once.
        self.page.wait_for_load_state('domcontentloaded')
        self.page.wait_for_timeout(150)

    def state(self, value):
        self.page.locator(f'.gather-login[data-login-state="{value}"]').wait_for()

    def ready(self, value='ready'):
        self.state(value)
        self.page.locator('.gather-login-loader').wait_for(state='hidden')
        check('form available after ' + value, self.page.locator('.gather-login-access').is_visible())
        check('finished loading is not busy', self.page.locator('.gather-login').get_attribute('aria-busy') == 'false')

    def close(self):
        self.context.close()


try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('ROBORT_BROWSER_EXECUTABLE'), headless=True)
        scene = Scene(browser)
        scene.context.add_init_script("""
          const decode = HTMLImageElement.prototype.decode;
          HTMLImageElement.prototype.decode = function() {
            return decode.call(this).then(() => new Promise(resolve => window.releaseTestDecode = resolve));
          };
        """)
        scene.open()
        scene.state('loading')
        check('desktop request is held and mobile not downloaded', scene.requests and set(scene.requests) == {'desktop'})
        check('form stays hidden while image request is pending', scene.page.locator('.gather-login-access').is_hidden())
        check('loading form is inert', scene.page.locator('.gather-login-access').evaluate('el=>el.inert'))
        scene.page.keyboard.press('Tab')
        check('keyboard cannot focus pending login controls', scene.page.evaluate("!document.querySelector('#login-form').contains(document.activeElement)"))
        scene.page.evaluate('document.activeElement.blur()')
        scene.page.screenshot(path=str(base / 'loading-desktop.png'))
        scene.release('desktop')
        scene.page.wait_for_function('typeof window.releaseTestDecode === "function"')
        check('waits for image decoding as well as network', scene.page.locator('.gather-login-access').is_hidden())
        scene.page.evaluate('window.releaseTestDecode()')
        scene.ready()
        scene.page.screenshot(path=str(base / 'loaded-desktop.png'))
        previous = len(scene.requests)
        scene.page.evaluate('renderLogin()')
        scene.ready()
        check('rerender reuses the decoded image', len(scene.requests) == previous)
        scene.close()

        scene = Scene(browser, 390, 844)
        scene.fail = True
        scene.open()
        scene.state('error')
        check('failure offers retry and continue', scene.page.get_by_role('button', name='重试加载').is_visible() and scene.page.get_by_role('button', name='继续登录').is_visible())
        scene.page.screenshot(path=str(base / 'loading-error-mobile.png'))
        scene.fail = False
        scene.serve.add('mobile')
        scene.page.get_by_role('button', name='重试加载').click()
        scene.ready()
        check('mobile only loads mobile artwork', set(scene.requests) == {'mobile'})
        scene.close()

        scene = Scene(browser, 390, 844)
        scene.open()
        scene.page.screenshot(path=str(base / 'loading-mobile.png'))
        scene.page.clock.fast_forward(16000)
        scene.state('slow')
        check('slow request keeps loading with optional continue', scene.page.locator('.gather-login-access').is_hidden() and scene.page.get_by_role('button', name='继续登录').is_visible())
        scene.page.get_by_role('button', name='继续登录').click()
        scene.ready('fallback')
        check('fallback focuses password field', scene.page.get_by_label('管理员密码', exact=True).evaluate('el=>el===document.activeElement'))
        check('fallback uses plain background', scene.page.locator('.gather-login').evaluate('el=>getComputedStyle(el).backgroundImage') == 'none')
        scene.release('mobile')
        scene.page.wait_for_load_state('networkidle')
        check('late image does not override explicit fallback', scene.page.locator('.gather-login').get_attribute('data-login-state') == 'fallback')
        scene.page.get_by_label('管理员密码', exact=True).fill(meta['password'])
        scene.page.get_by_role('button', name='登录工作台', exact=True).click()
        scene.page.locator('.metric-grid').wait_for()
        check('fallback login reaches authenticated workspace', scene.page.locator('.gather-login-loader').count() == 0)
        scene.close()

        scene = Scene(browser)
        scene.open()
        scene.page.set_viewport_size({'width': 390, 'height': 844})
        scene.page.wait_for_timeout(150)
        check('resize requests mobile asset', 'mobile' in scene.requests)
        scene.release('desktop')
        scene.page.wait_for_timeout(150)
        check('old desktop completion cannot reveal pending mobile screen', scene.page.locator('.gather-login-access').is_hidden())
        scene.release('mobile')
        scene.ready()
        scene.page.get_by_label('管理员密码', exact=True).fill('preserve-local-value')
        scene.page.set_viewport_size({'width': 1280, 'height': 900})
        scene.ready()
        check('resize retains entered form value', scene.page.get_by_label('管理员密码', exact=True).input_value() == 'preserve-local-value')
        scene.close()

        scene = Scene(browser, reduced_motion='reduce')
        scene.open()
        check('reduced motion disables all loading animations', scene.page.locator('.gather-loading-orbit,.gather-loading-symbol>span,.gather-loading-track span').evaluate_all('els=>els.every(el=>getComputedStyle(el).animationName==="none")'))
        scene.release('desktop')
        scene.ready()
        scene.close()

        scene = Scene(browser)
        login = scene.context.request.post(origin + '/auth/login', data={'password': meta['password']})
        check('restored session fixture authenticated', login.status == 200)
        scene.page.goto(origin + '/admin#dashboard', wait_until='domcontentloaded')
        scene.page.locator('.metric-grid').wait_for()
        check('existing session bypasses pending login artwork', scene.page.locator('.gather-login-loader').count() == 0)
        scene.release('desktop')
        scene.page.set_viewport_size({'width': 390, 'height': 844})
        scene.page.wait_for_timeout(150)
        check('disposed login does not reopen or fetch mobile artwork', scene.page.locator('.gather-login-loader').count() == 0 and 'mobile' not in scene.requests)
        scene.close()
        browser.close()
    check('no browser errors', not report['errors'])
    check('no external requests', not report['external_requests'])
finally:
    (base / 'loading-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'checks': len(report['checks']), 'errors': report['errors'], 'external_requests': report['external_requests']}, ensure_ascii=False))
