"""Remove presentation from inbound text without executing or fetching markup."""
import html
from html.parser import HTMLParser
import re
import uuid


_INLINE = {'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'span',
           'font', 'small', 'big', 'mark', 'code', 'a', 'sub', 'sup'}
_BLOCK = {'p', 'div', 'section', 'blockquote', 'pre', 'ul', 'ol', 'li',
          'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_UNSUPPORTED = {'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math',
                'img', 'video', 'audio', 'input', 'button', 'template'}
_ERROR = '消息包含无法转换的内容，请发送文字指令，例如：报名 或 名单。'


class _TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        properties = dict(attrs)
        style = re.sub(r'\s+', '', properties.get('style') or '').lower()
        if (tag in _UNSUPPORTED or 'hidden' in properties or
                (properties.get('aria-hidden') or '').lower() == 'true' or
                re.search(r'(?:display:none|visibility:hidden|opacity:0(?:;|$)|font-size:0(?:px|pt|em|;|$))', style)):
            raise ValueError(_ERROR)
        if tag in _BLOCK or tag in {'br', 'hr'}:
            self.parts.append('\n')
        elif tag not in _INLINE:
            # Keep mentions, literal attributes and unknown tags intact.
            self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in _UNSUPPORTED:
            raise ValueError(_ERROR)
        if tag in _BLOCK:
            self.parts.append('\n')
        elif tag not in _INLINE and tag not in {'br', 'hr'}:
            self.parts.append('</' + tag + '>')

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_comment(self, data):
        raise ValueError(_ERROR)

    def handle_decl(self, decl):
        raise ValueError(_ERROR)

    def handle_pi(self, data):
        raise ValueError(_ERROR)

    def unknown_decl(self, data):
        raise ValueError(_ERROR)


def clean_styles(text, bot_name=''):
    """A bounded subset of chat formatting, not a general Markdown renderer.

    Preserve newlines, literal URLs, escaped punctuation and embedded user
    mentions. Never scan for an action or turn image/HTML attributes into one.
    """
    marker = 'ROBORTSTYLE' + uuid.uuid4().hex
    saved = []

    def protect(value):
        saved.append(value)
        return marker + str(len(saved)-1) + 'END'

    # Decode transport entities exactly once, including encoded style tags.
    text = html.unescape(text).replace('\r\n', '\n').replace('\r', '\n')
    text = text.translate({ord(char): None for char in '\u200b\u2060\ufeff\u00ad'})
    if bot_name:
        letters = ''.join(html.unescape(bot_name).split())
        if letters:
            label = r'[^\S\r\n]*'.join(re.escape(char) for char in letters)
            text = re.sub(r'[@＠][^\S\r\n]*'+label+r'(?=\s|[:：,，<*]|$)', lambda m: protect(m[0]), text)
    # Code is literal content. Its wrapper can be discarded without parsing
    # nested tags or punctuation in identifiers as a second formatting layer.
    text = re.sub(r'(?m)(?:^|(?<=>)) {0,3}(`{3,}|~{3,})[\w+-]*[ \t]*\n(.*?)\n {0,3}\1[ \t]*(?=\n|<|$)',
                  lambda m: protect(m[2]), text, flags=re.S)
    text = re.sub(r'(`+)([^`\n]+)\1', lambda m: protect(m[2]), text)
    text = re.sub(r'\\([\\`*_{}\[\]()#+\-.!>~])', lambda m: protect(m[1]), text)
    # HTMLParser treats an unfinished entity such as A&B as markup. Entities
    # were already decoded above; protect every remaining ampersand literally.
    text = text.replace('&', protect('&'))
    parser = _TextHTML()
    parser.feed(text)
    parser.close()
    text = ''.join(parser.parts)
    text = re.sub(r'<at\b[^<>]*>[^<>]*</at\s*>', lambda m: protect(m[0]), text, flags=re.I)
    if re.search(r'!\[[^\]\n]*\]\(', text):
        raise ValueError(_ERROR)
    # Link captions are visible text; destinations never become commands.
    text = re.sub(r'\[([^\]\n]+)\]\((?:[^()\n]|\([^()\n]*\))*\)', lambda m: m[1], text)
    text = re.sub(r'https?://[^\s<>]+', lambda m: protect(m[0]), text)
    lines = []
    for line in text.split('\n'):
        line = re.sub(r'^ {0,3}(?:>\s*)+', '', line)
        line = re.sub(r'^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$', r'\1', line)
        line = re.sub(r'^ {0,3}(?:[-+*]|[0-9]{1,3}[.)、])\s+(?:\[[ xX]\]\s+)?', '', line)
        lines.append(line)
    text = '\n'.join(lines)
    # Underscores within account names are ordinary characters, not emphasis.
    patterns = (r'\*\*([^*\n]+)\*\*', r'(?<!\w)__([^_\n]+)__(?!\w)',
                r'~~([^~\n]+)~~', r'\*([^*\n]+)\*', r'(?<!\w)_([^_\n]+)_(?!\w)')
    for _ in range(8):
        previous = text
        for pattern in patterns:
            text = re.sub(pattern, r'\1', text)
        if text == previous:
            break
    for index, value in reversed(list(enumerate(saved))):
        text = text.replace(marker + str(index) + 'END', value)
    return text.strip()
