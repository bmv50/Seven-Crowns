"""Convert legacy Telegram markup to escaped MAX HTML, never trust raw HTML."""
import html
import re
from urllib.parse import urlsplit


def to_html(value):
    source = str(value)
    out = []
    i = 0
    while i < len(source):
        c = source[i]
        if c == '\\' and i+1 < len(source) and source[i+1] in '\\*_`[]':
            out.append(html.escape(source[i+1])); i += 2; continue
        if c == '`':
            end = source.find('`', i+1)
            if end >= 0:
                out.append('<code>'+html.escape(source[i+1:end])+'</code>'); i = end+1; continue
            i += 1; continue
        if c == '[':
            match = re.match(r'\[([^\]\n]+)\]\(([^)\s]+)\)', source[i:])
            if match:
                label, url = match.groups()
                try:
                    safe = urlsplit(url).scheme in {'https', 'http'}
                except ValueError:
                    safe = False
                if safe:
                    out.append('<a href="'+html.escape(url, quote=True)+'">'+html.escape(label)+'</a>')
                else:
                    out.append(html.escape(label))
                i += len(match.group()); continue
        if c in '*_':
            # Underscores inside IDs are literal, not Telegram italic markers.
            if c == '_' and i and source[i-1].isalnum():
                out.append('_'); i += 1; continue
            mark = c*2 if source[i:i+2] == c*2 else c
            end = source.find(mark, i+len(mark))
            if end > i+len(mark) and '\n' not in source[i:end]:
                body = source[i+len(mark):end]
                tag = 'b' if c == '*' or len(mark) == 2 else 'i'
                out.append('<'+tag+'>'+html.escape(re.sub(r'\\([*_`\[\\])', r'\1', body))+'</'+tag+'>')
                i = end+len(mark); continue
            # Discard unmatched legacy emphasis, including double-escaped stars.
            if c == '_':
                out.append('_')
            i += len(mark); continue
        out.append(html.escape(c)); i += 1
    result = ''.join(out)
    lines = result.split('\n')
    if lines and len(lines[0]) < 160 and '<' not in lines[0] and re.match(r'^(🎒|📍|👤|🗺|💬|🐾|📜|✨|⚙️|🏪|⚔️|🔍|🔒|🛟|📄|🏰|⚖️|👥|Семь Корон)', lines[0]):
        lines[0] = '<b>'+lines[0]+'</b>'
    return '\n'.join(lines)


def parts(value):
    source = str(value)
    result = []
    while source:
        size = min(3500, len(source))
        while len(to_html(source[:size])) > 3500:
            size = max(1, size*3//4)
        if size < len(source):
            newline = source.rfind('\n', 0, size)
            if newline > size//2:
                size = newline+1
        result.append(source[:size])
        source = source[size:]
    return result
