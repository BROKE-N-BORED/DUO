"""The bounded Markdown-to-HTML converter for the panes.

Split out of dual_chat.py in v0.25 (audit T14). Display layer
only: history, exports, conversation files, and the phone deck
all keep the raw text.
"""

import html
import re


# Markdown rendering (v0.24, audit T13): model replies are shown
# formatted in the panes -- bold, italic, code, headers, lists,
# quotes, links -- through the panes' QTextEdit rich-text support.
# This is a DISPLAY layer only: history, exports, conversation
# files, and the phone deck all keep the raw text, and user
# messages are never reformatted (only assistant messages, plus
# the summary entry, which is model output stored as a user entry).
#
# The converter is deliberately bounded: the subset below, nothing
# more. It HTML-escapes everything FIRST and only then applies the
# Markdown transforms, so a reply containing raw HTML (a "<b>" tag,
# a "<script>") shows as text and can never inject markup. Links
# are http/https only -- anything else renders as literal text.
# Inline spans never cross a line break, and an unclosed delimiter
# stays literal text rather than swallowing the rest of the message.
# A code block's fence runs to the end of the message when the
# closing fence is missing (standard Markdown behaviour).

_MD_CODE_BG = "#f0f0f0"  # code-block background on the light panes


_MD_INLINE_RE = re.compile(
    r"`([^`\n]+)`"  # code span (first: nothing inside is formatted)
    r"|\[([^\]\n]+)\]\(([^)\n]+)\)"  # link [text](url)
    r"|\*\*(\S(?:.*?\S)?)\*\*"  # bold **text**
    r"|\*(\S(?:.*?\S)?)\*"  # italic *text*
    r"|(?<!\w)_(\S(?:.*?\S)?)_(?!\w)")  # italic _text_ (not snake_case)


def _markdown_inline(line):
    """Render the inline Markdown of one (already escaped) line:
    code spans, links, bold, italic. The alternation order is the
    precedence: code wins over everything inside it, bold wins over
    italic for a "**" pair. Content must start and end with a
    non-space character, so "2 * 3 * 4" is never italicised."""
    def _replace(match):
        code, link_text, link_url, bold, ital_star, ital_under = (
            match.groups())
        if code is not None:
            return "<code>%s</code>" % code
        if link_text is not None:
            if (link_url.startswith("http://")
                    or link_url.startswith("https://")):
                return ('<a href="%s">%s</a>' % (link_url, link_text))
            return match.group(0)
        if bold is not None:
            return "<b>%s</b>" % bold
        if ital_star is not None:
            return "<i>%s</i>" % ital_star
        return "<i>%s</i>" % ital_under

    return _MD_INLINE_RE.sub(_replace, line)


def markdown_to_html(text):
    """Convert the bounded Markdown subset to HTML for a pane.

    Blocks, split on blank lines: fenced code blocks (```), headers
    (# / ## / ###), unordered lists (- / *), ordered lists (1. ),
    blockquotes (>), and paragraphs. A paragraph's lines are joined
    with <br>, so line breaks show exactly as typed (a raw newline
    inside a <p> would collapse to a space). Paragraph gaps render
    as one newline in the pane's plain text -- the <p> margins give
    the visual separation. Returns "" for empty or blank input."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return ""
    lines = text.split("\n")
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            # Fenced code block: raw lines, escaped, no inline
            # formatting inside. An unclosed fence runs to the end.
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # skip the closing fence
            blocks.append(("code", "\n".join(code_lines)))
            continue
        header = re.match(r"^(#{1,3}) ", stripped)
        if header:
            level = len(header.group(1))
            blocks.append(("header", level, _markdown_inline(
                html.escape(stripped[level + 1:]))))
            i += 1
            continue
        if re.match(r"^[-*] ", stripped):
            items = []
            while i < len(lines):
                item = lines[i].strip()
                if not re.match(r"^[-*] ", item):
                    break
                items.append(_markdown_inline(html.escape(item[2:])))
                i += 1
            blocks.append(("ul", items))
            continue
        if re.match(r"^\d+[.)] ", stripped):
            items = []
            while i < len(lines):
                item = lines[i].strip()
                if not re.match(r"^\d+[.)] ", item):
                    break
                items.append(_markdown_inline(
                    html.escape(re.sub(r"^\d+[.)] ", "", item))))
                i += 1
            blocks.append(("ol", items))
            continue
        if stripped.startswith(">"):
            quote_lines = []
            while i < len(lines):
                quote = lines[i].strip()
                if not quote.startswith(">"):
                    break
                quote_lines.append(quote[1:].strip())
                i += 1
            blocks.append(("quote", _markdown_inline(
                html.escape(" ".join(quote_lines)))))
            continue
        para_lines = []
        while i < len(lines):
            para = lines[i]
            if not para.strip():
                break
            if (para.strip().startswith("```")
                    or re.match(r"^#{1,3} ", para.strip())
                    or re.match(r"^[-*] ", para.strip())
                    or re.match(r"^\d+[.)] ", para.strip())
                    or para.strip().startswith(">")):
                break
            para_lines.append(_markdown_inline(html.escape(para)))
            i += 1
        blocks.append(("para", "<br>".join(para_lines)))

    out = []
    for block in blocks:
        kind = block[0]
        if kind == "code":
            out.append('<pre style="background-color:%s;">%s</pre>'
                       % (_MD_CODE_BG, html.escape(block[1])))
        elif kind == "header":
            out.append("<h%d>%s</h%d>" % (block[1], block[2], block[1]))
        elif kind == "ul":
            out.append("<ul>%s</ul>" % "".join(
                "<li>%s</li>" % item for item in block[1]))
        elif kind == "ol":
            out.append("<ol>%s</ol>" % "".join(
                "<li>%s</li>" % item for item in block[1]))
        elif kind == "quote":
            out.append("<blockquote>%s</blockquote>" % block[1])
        else:
            out.append("<p>%s</p>" % block[1])
    return "".join(out)
