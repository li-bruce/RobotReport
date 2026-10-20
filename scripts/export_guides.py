"""Export the current web manuals as Markdown with repository-local screenshots."""
import argparse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def walk(node, tag):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag == tag:
                yield child
            yield from walk(child, tag)


def href(value):
    if value.startswith('/static/guide-images/'):
        return '../app/static/guide-images/' + value.rsplit('/', 1)[1]
    if value.startswith('/guide/admin'):
        return value.replace('/guide/admin', 'ADMIN_GUIDE.zh-CN.md', 1)
    if value.startswith('/guide/member'):
        return value.replace('/guide/member', 'MEMBER_GUIDE.zh-CN.md', 1)
    if value.startswith('/guide'):
        return value.replace('/guide', 'MEMBER_GUIDE.zh-CN.md', 1)
    return value


def render(node):
    if isinstance(node, str):
        return re.sub(r'\s+', ' ', node)
    tag = node.tag
    content = ''.join(render(child) for child in node.children)
    if tag in ('script', 'style', 'button'):
        return ''
    if tag == 'br':
        return '<br>'
    if tag == 'strong':
        return '**' + content.strip() + '**'
    if tag == 'code':
        return '`' + content.strip() + '`'
    if tag == 'div' and set(node.attrs.get('class', '').split()) & {'command-stack', 'command-block', 'guide-jumps'}:
        items = [render(child).strip() for child in node.children if isinstance(child, Node)]
        return '\n\n' + '\n'.join('- ' + item for item in items if item) + '\n\n'
    if tag == 'li':
        meaningful = [c for c in node.children if not isinstance(c, str) or c.strip()]
        if len(meaningful) == 2 and all(isinstance(c, Node) for c in meaningful) and meaningful[0].tag == 'strong' and meaningful[1].tag == 'span':
            return render(meaningful[0]) + '：' + render(meaningful[1])
    if tag in ('h2', 'h3', 'h4'):
        return '\n\n' + '#' * int(tag[1]) + ' ' + content.strip() + '\n\n'
    if tag == 'a':
        target = href(node.attrs.get('href', ''))
        if any(isinstance(c, Node) and c.tag == 'img' for c in node.children):
            return content
        if target.startswith('/admin'):
            return content.strip() + '（后台 `' + target + '`）'
        return '[' + content.strip() + '](' + target + ')'
    if tag == 'img':
        return '\n\n![' + node.attrs.get('alt', '') + '](' + href(node.attrs['src']) + ')\n\n'
    if tag in ('ol', 'ul'):
        items = [c for c in node.children if isinstance(c, Node) and c.tag == 'li']
        return '\n\n' + '\n'.join(
            (str(i + 1) + '. ' if tag == 'ol' else '- ') + render(c).strip().replace('\n', ' ')
            for i, c in enumerate(items)
        ) + '\n\n'
    if tag == 'table':
        rows = []
        for row in walk(node, 'tr'):
            cells = [render(c).strip().replace('\n', ' ').replace('|', '\\|')
                     for c in row.children if isinstance(c, Node) and c.tag in ('th', 'td')]
            rows.append('| ' + ' | '.join(cells) + ' |')
            if len(rows) == 1:
                rows.append('| ' + ' | '.join('---' for _ in cells) + ' |')
        return '\n\n' + '\n'.join(rows) + '\n\n'
    if tag == 'dt':
        return '\n\n**' + content.strip() + '**：'
    if tag == 'dd':
        return content.strip() + '\n'
    if tag == 'details':
        summary = next((c for c in node.children if isinstance(c, Node) and c.tag == 'summary'), None)
        title = render(summary).strip() if summary else '查看详情'
        body = ''.join(render(c) for c in node.children if c is not summary).strip()
        return '\n\n<details>\n<summary>' + title + '</summary>\n\n' + body + '\n\n</details>\n\n'
    if tag in ('p', 'figure', 'figcaption'):
        return '\n\n' + content.strip() + '\n\n'
    if tag in ('section', 'article') and node.attrs.get('id'):
        return '\n\n<a id="' + node.attrs['id'] + '"></a>\n\n' + content
    if 'command-line' in node.attrs.get('class', '').split():
        return '\n\n- ' + content.strip() + '\n'
    if tag == 'span':
        if 'chapter-number' in node.attrs.get('class', ''):
            return ''
        return content + ' '
    return content


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    for source, output, title, url in (
        ('guide.html', 'MEMBER_GUIDE.zh-CN.md', '普通用户操作说明', '/guide/member'),
        ('guide-admin.html', 'ADMIN_GUIDE.zh-CN.md', '管理员操作说明', '/guide/admin'),
    ):
        tree = Document((ROOT / 'app/static' / source).read_text(encoding='utf-8'))
        chapters = [c for c in walk(tree.root, 'section') if 'chapter' in c.attrs.get('class', '').split()]
        text = f'# {title}\n\nv1.0 · 2026-10-20。服务中的网页版：`{url}`。\n\n按当前页面编写；截图均为隔离环境中的虚构数据。图片可点开查看原尺寸。示例姓名、日期、场次请按实际替换。\n\n'
        text += '\n'.join(render(chapter) for chapter in chapters)
        text = re.sub(r'\n[ \t]+', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'
        text = '\n'.join(line.rstrip() for line in text.splitlines()) + '\n'
        (ROOT / 'docs' / output).write_text(text, encoding='utf-8')
        print(output)


if __name__ == '__main__':
    main()
