"""Transcript files: format the plain-text exports (one side,
both sides, full log), name the files, and parse a saved
single-pane transcript back into entries.

Split out of dual_chat.py in v0.25 (audit T14). The formatters
take a side object and read its labels/display text -- they
never import the model, so this module stays decoupled from
it; only the summary label is shared (from duo_model).
"""

import datetime
import re

from duo_model import SUMMARY_LABEL


# Transcripts (Item 7): plain-text export. The file reads like the
# window -- the same labels and short display texts, one blank line
# between messages. Attached file contents are never pasted in; the
# short "(Attached: ...)" tag is all the file carries.

def sanitize_filename_part(text):
    """Make a model name safe for a filename: keep letters, digits,
    '.', '-' and '_'; everything else becomes '-'."""
    cleaned = "".join(
        ch if (ch.isalnum() or ch in "._-") else "-" for ch in text)
    return cleaned.strip(".-") or "model"


def default_side_filename(side, model, today=None):
    """Suggested filename for one side's export, e.g.
    "duo-model-a-fake-a-latest-2026-11-12.txt". No model picked ->
    "duo-model-a-2026-11-12.txt"."""
    day = (today or datetime.date.today()).strftime("%Y-%m-%d")
    base = "duo-model-%s" % side.lower()
    if model:
        base += "-" + sanitize_filename_part(model)
    return "%s-%s.txt" % (base, day)


def default_both_filename(today=None):
    """Suggested filename for the combined export, e.g.
    "duo-both-2026-11-12.txt"."""
    day = (today or datetime.date.today()).strftime("%Y-%m-%d")
    return "duo-both-%s.txt" % day


def default_full_filename(today=None):
    """Suggested filename for the full-transcript export, e.g.
    "duo-full-2026-11-12.txt"."""
    day = (today or datetime.date.today()).strftime("%Y-%m-%d")
    return "duo-full-%s.txt" % day


def default_debug_filename(today=None):
    """Suggested filename for the debug report export, e.g.
    "duo-debug-report-2026-11-12.txt"."""
    day = (today or datetime.date.today()).strftime("%Y-%m-%d")
    return "duo-debug-report-%s.txt" % day


def default_conversation_filename(today=None):
    """Suggested filename for a saved conversation, e.g.
    "duo-conversation-2026-11-12.json"."""
    day = (today or datetime.date.today()).strftime("%Y-%m-%d")
    return "duo-conversation-%s.json" % day


def _saved_stamp(when):
    moment = when or datetime.datetime.now()
    return moment.strftime("%Y-%m-%d %H:%M")


def _side_blocks(side_obj):
    """One text block per message, exactly as the window shows it:
    "label: display text", with the short display text (attachment
    tag, handoff note) instead of the full contents."""
    return ["%s: %s" % (label, side_obj.display_text(i))
            for i, label in enumerate(side_obj.labels)]


def format_side_transcript(side_obj, when=None):
    """The plain-text export for one side: a header line naming DUO,
    the model, and when the file was saved, then each message in order,
    one blank line between messages. An empty transcript gets the
    header plus "No messages yet."."""
    model = side_obj.model or "no model picked"
    header = ("DUO transcript -- Model %s (%s) -- saved %s"
              % (side_obj.name, model, _saved_stamp(when)))
    if not side_obj.history:
        return header + "\n\nNo messages yet.\n"
    return "\n\n".join([header] + _side_blocks(side_obj)) + "\n"


def format_both_transcript(convo, when=None):
    """The combined export: one header line naming DUO and when the
    file was saved, then a "Model A" section and a "Model B" section,
    each in message order. Each section names its model; an empty
    side gets "No messages yet."."""
    parts = ["DUO transcript -- saved %s" % _saved_stamp(when)]
    for side in ("A", "B"):
        side_obj = convo.sides[side]
        model = side_obj.model or "no model picked"
        parts.append("Model %s (%s)" % (side, model))
        if not side_obj.history:
            parts.append("No messages yet.")
        else:
            parts.extend(_side_blocks(side_obj))
    return "\n\n".join(parts) + "\n"


def format_full_transcript(log, when=None):
    """The full transcript: every logged message in arrival order,
    both sides interleaved as the conversation actually happened.
    Each message is prefixed with its side ("A -- You: ...",
    "B -- <model>: ..."), one blank line between messages. An empty
    log gets the header plus "No messages yet."."""
    header = "DUO transcript -- saved %s" % _saved_stamp(when)
    if not log:
        return header + "\n\nNo messages yet.\n"
    blocks = ["%s -- %s: %s" % (side, label, text)
              for side, label, text in log]
    return "\n\n".join([header] + blocks) + "\n"


class TranscriptError(Exception):
    """A transcript file can't be imported: the wrong format, or no
    messages DUO can read. The message is plain text for the user."""



# A "You" label: the user, optionally with a [relay from ...] /
# [task] / [task handoff] tag. The tag is part of the label.
_LABEL_YOU = re.compile(r"^You( \[[^\]]*\])?: (.*)$")


# A model label: a single word, optionally with a :tag suffix.
_LABEL_MODEL = re.compile(r"^([A-Za-z0-9._\-/]+(?::[A-Za-z0-9._\-]+)?): (.*)$")


def _parse_transcript_block(block):
    """Parse one message block into (role, content, label), or None
    when the block doesn't start with a sender label. The body is the
    rest of the block, multi-line bodies included."""
    first, _, rest = block.partition("\n")
    match = _LABEL_YOU.match(first)
    if match:
        label = "You" + (match.group(1) or "")
        body = match.group(2)
        role = "user"
    elif first.startswith(SUMMARY_LABEL + ": "):
        label = SUMMARY_LABEL
        body = first[len(SUMMARY_LABEL) + 2:]
        role = "user"
    else:
        match = _LABEL_MODEL.match(first)
        if not match:
            return None
        label = match.group(1)
        body = match.group(2)
        role = "assistant"
    if rest:
        body += "\n" + rest
    return (role, body, label)


def parse_side_transcript(text):
    """Parse a saved single-pane transcript (the v0.7 export format)
    into a list of {role, content, label} entries, in file order.

    The header line and the "No messages yet." line are skipped.
    Messages are split on blank lines; a block with no sender label
    is a continuation of the previous message (a blank line inside
    its body). Labels starting with "You" are the user's, anything
    else is the model's, and the label text is kept exactly.

    A "Save both" file or a full-transcript file is rejected by
    structure, not by filename: their headers name no single model,
    and their blocks start with a "Model A (...)" section header or
    an "A -- " / "B -- " side prefix. Raises TranscriptError for
    any file that isn't a single-pane transcript with messages.

    Note what an export carries: the text the window showed
    (display_text), not the full message contents -- attached file
    contents are never pasted in, and a handoff shows its short
    note, not the framed text. So an imported message's content is
    what the window showed, which is what the model will see if it
    is sent again."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines or not lines[0].startswith("DUO transcript -- "):
        raise TranscriptError(
            "this doesn't look like a DUO transcript file")
    header = lines[0]
    single = re.match(
        r"^DUO transcript -- Model [AB] \(.*\) -- saved ", header)
    rest = "\n".join(lines[1:])
    # Blocks are separated by blank lines; the blank line after the
    # header leaves a leading newline on the first block. Strip
    # newlines only -- a message body can end with a space (streamed
    # replies do), and that space is content, not separator.
    blocks = [block.strip("\n")
              for block in rest.split("\n\n") if block.strip()]
    if not single:
        for block in blocks:
            first = block.split("\n", 1)[0]
            if re.match(r"^Model [AB] \(.*\)$", first):
                raise TranscriptError(
                    "this file holds both sides -- import a "
                    "single-side transcript instead")
            if first.startswith("A -- ") or first.startswith("B -- "):
                raise TranscriptError(
                    "this is a full transcript -- import a "
                    "single-side transcript instead")
        raise TranscriptError("this file has no messages in it")
    entries = []
    for block in blocks:
        if block.strip() == "No messages yet.":
            continue
        parsed = _parse_transcript_block(block)
        if parsed is None:
            if entries:
                role, content, label = entries[-1]
                entries[-1] = (role, content + "\n\n" + block, label)
            else:
                raise TranscriptError(
                    "this file has no messages DUO can read")
        else:
            entries.append(parsed)
    if not entries:
        raise TranscriptError("this file has no messages in it")
    return [{"role": role, "content": content, "label": label}
            for role, content, label in entries]


def load_side_transcript_file(path):
    """Read a saved single-pane transcript file and parse it. Raises
    TranscriptError for a bad format, OSError for a file that can't
    be read."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    return parse_side_transcript(text)
