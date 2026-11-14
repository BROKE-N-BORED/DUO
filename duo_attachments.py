"""File attachments for DUO (Item 5).

Moved verbatim from dual_chat.py in v0.42 (redesign U20): the window
file was 8 lines under its 4,000-line bound, and this block -- the
limits, the error, and the read/build/tag helpers -- is
self-contained, stdlib only. Contents are read at attach time, so a
file deleted before the send still goes through.
"""

import os

# Attachments (Item 5): text files only, each at most 100 KB, at most
# 5 attached at once. Contents are read at attach time, so a file
# deleted before the send still goes through.
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 100 * 1024


class AttachmentError(Exception):
    """A plain-language reason a file couldn't be attached."""


def load_attachment(path):
    """Read a file as an attachment: (name, text).

    Raises AttachmentError with a plain message when the file is too
    big, isn't decodable UTF-8, or can't be read. The size is checked
    before reading, so a huge file is refused without loading it.
    """
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        raise AttachmentError("Couldn't read '%s'." % name)
    if size > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(
            "'%s' is over 100 KB, so it wasn't attached." % name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        raise AttachmentError(
            "'%s' isn't a text file, so it wasn't attached." % name)
    return name, text


def build_attached_message(files, text):
    """Prepend file blocks to a typed message.

    files is [(name, contents), ...]. Each file becomes a block
    "File: <name>\n<contents>"; blocks and the typed message are
    separated by a blank line. No files -> the message unchanged.
    """
    if not files:
        return text
    blocks = ["File: %s\n%s" % (name, contents) for name, contents in files]
    return "\n\n".join(blocks) + "\n\n" + text


def attachment_tag(names):
    """The short transcript tag for attached files."""
    return "(Attached: %s)" % ", ".join(names)
