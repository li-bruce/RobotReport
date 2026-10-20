"""Public guide regressions: navigation, real feature names, and private-data boundary."""
import ast
from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'app' / 'static'


class GuideDocument(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.links = []
        self.resources = []
        self.viewports = []
        self.headings = []
        self.hidden_articles = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.links.append(attrs.get('href', ''))
        if tag in ('link', 'script', 'img'):
            self.resources.append(attrs.get('src') or attrs.get('href'))
        if tag == 'meta' and attrs.get('name') == 'viewport':
            self.viewports.append(attrs.get('content'))
        if tag == 'h1':
            self.headings.append(tag)
        if tag == 'article' and 'hidden' in attrs:
            self.hidden_articles.append(attrs)


def test_guide_routes_and_anchors_are_complete_without_javascript():
    for filename in ('guide.html','guide-admin.html'):
        page=GuideDocument((STATIC / filename).read_text(encoding='utf-8'))
        assert len(page.ids)==len(set(page.ids))
        assert all(link[1:] in page.ids for link in page.links if link.startswith('#'))
        assert page.headings==['h1'] and not page.hidden_articles
        assert '/guide' in page.links and '/guide/admin' in page.links
        assert page.viewports==['width=device-width, initial-scale=1']
    text = (STATIC / 'guide.html').read_text(encoding='utf-8')
    page = GuideDocument(text)
    assert len(page.ids) == len(set(page.ids)), 'duplicate HTML IDs break anchored navigation'
    assert {'content', 'start', 'members', 'competitions', 'management'} <= set(page.ids)
    assert page.headings == ['h1']
    assert not page.hidden_articles, 'all instructions must be readable when JavaScript is disabled'
    assert all(link[1:] in page.ids for link in page.links if link.startswith('#'))
    assert page.viewports == ['width=device-width, initial-scale=1']
    assert not any('user-scalable=no' in value or 'maximum-scale=1' in value for value in page.viewports)
    assert all(resource.startswith('/static/guide.') for resource in page.resources)


def test_guide_competition_names_match_available_generators():
    text = (STATIC / 'guide.html').read_text(encoding='utf-8')
    chapter = text.split('id="competitions"', 1)[1].split('id="management"', 1)[0]
    metadata = []
    for path in (ROOT / 'app' / 'formats').glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'META' for target in node.targets):
                metadata.append(ast.literal_eval(node.value))
    assert len(metadata) >= 7
    for item in metadata:
        assert f'<h4>{item["name"]}</h4>' in chapter
    assert '五人转、五羽伦比不用于混双' in chapter
    assert '授权期内登记新比分' in chapter
    assert '关闭后保留历史，不能恢复' in chapter


def test_public_guide_does_not_fetch_private_records_or_include_deployment_credentials():
    html = (STATIC / 'guide.html').read_text(encoding='utf-8')
    admin = (STATIC / 'guide-admin.html').read_text(encoding='utf-8')
    script = (STATIC / 'guide.js').read_text(encoding='utf-8')
    # This shareable document must remain static even if opened by an admin with cookies.
    assert not re.search(r'\b(fetch|XMLHttpRequest|WebSocket|EventSource)\b', script)
    assert '/api/' not in html + admin + script
    assert '/kingsoft/callback/' not in html + admin
    assert not re.search(r'https?://|\bsk-[a-zA-Z0-9]{12,}|81\.70\.37\.17', html + admin + script)
    assert '管理员密码' in admin
    assert '不是各自的网页登录账号' in admin
    assert '重名时填写登记手机号区分' in html and '确认同名资料即可注册' in html


def test_guide_admin_links_resolve_to_existing_navigation():
    html = (STATIC / 'guide.html').read_text(encoding='utf-8')+(STATIC / 'guide-admin.html').read_text(encoding='utf-8')
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    for route in re.findall(r'href="/admin#([\w-]+)"', html):
        assert re.search(rf"['\"]{re.escape(route)}['\"]", app), f'unknown admin page {route}'
    for command in ('审核通过 申请12', '审核拒绝 申请12', '报名，张三，李四', '我的会籍', '我的对战统计'):
        assert command in html
