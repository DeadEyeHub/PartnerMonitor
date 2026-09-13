"""Escaped HTML primitives shared by report sections."""
import html
from .inspection import display_label

def esc(value):
    return html.escape('' if value is None else str(value),quote=True)
def table(rows,exclude=()):
    if not rows:
        return '<p class="muted">No records. Check the source status.</p>'
    columns = [c for c in rows[0] if c not in exclude and not c.endswith('_json') and c!='limitations']
    def cell(value):
        return '<br>'.join('<a href="'+esc(line)+'">'+esc(line)+'</a>' if line.startswith(('https://','http://')) else esc(line)
            for line in str('' if value is None else value).split('\n'))
    return '<div class="scroll"><table><thead><tr>'+''.join('<th title="'+esc(c)+'">'+esc(display_label(c))+'</th>' for c in columns)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+cell(row.get(c))+'</td>' for c in columns)+'</tr>' for row in rows)+'</tbody></table></div>'
